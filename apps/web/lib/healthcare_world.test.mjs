// Frontend logic tests for the Healthcare Information World.
// Run with `node --test` (Node 22, no install required).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ROLES,
  CAPABILITIES,
  PRIVACY_BADGE,
  BLOCKED_CAPABILITIES,
  emergencyBanner,
  detectEmergencyLanguage,
  isBlockedCapability,
  trainingEligible,
  redactPHIFlags,
  maskPHI,
  buildTimeline,
  appointmentQuestions,
  medicationQuestions,
  outputDisclosures,
  safeOutputView,
  DEMO_DOC,
  DEMO_TIMELINE,
  DEMO_TERMS,
} from "./healthcare_world.mjs";

test("emergencyBanner gives country-specific numbers and a safe default", () => {
  assert.match(emergencyBanner("US"), /911/);
  assert.match(emergencyBanner("uk"), /999/);
  assert.match(emergencyBanner("AU"), /000/);
  const unknown = emergencyBanner("ZZ");
  assert.match(unknown, /local emergency services/);
  // Never empty, even with no argument.
  assert.ok(emergencyBanner().length > 0);
});

test("detectEmergencyLanguage flags red-flag phrases only", () => {
  assert.equal(detectEmergencyLanguage("I have chest pain and can't breathe"), true);
  assert.equal(detectEmergencyLanguage("mild seasonal cough"), false);
  assert.equal(detectEmergencyLanguage(""), false);
});

test("isBlockedCapability catches diagnosis/prescription/dosage/dispatch", () => {
  assert.equal(isBlockedCapability("diagnosis"), true);
  assert.equal(isBlockedCapability("diagnose the patient"), true);
  assert.equal(isBlockedCapability("prescribe medication"), true);
  assert.equal(isBlockedCapability("change dosage"), true);
  assert.equal(isBlockedCapability("dosage_change"), true);
  assert.equal(isBlockedCapability("emergency dispatch"), true);
  assert.equal(isBlockedCapability("dispatch-emergency"), true);
});

test("isBlockedCapability allows benign informational actions", () => {
  assert.equal(isBlockedCapability("explain_terms"), false);
  assert.equal(isBlockedCapability("summarize document"), false);
  assert.equal(isBlockedCapability("organize timeline"), false);
  assert.equal(isBlockedCapability(""), false);
});

test("trainingEligible is ALWAYS false for healthcare items", () => {
  assert.equal(trainingEligible(DEMO_DOC), false);
  assert.equal(trainingEligible({ domain: "healthcare", consent: true }), false);
  assert.equal(trainingEligible(null), false);
  assert.equal(PRIVACY_BADGE.trainingEligibleByDefault, false);
  assert.equal(PRIVACY_BADGE.isolatedFromGeneralMemory, true);
});

test("redactPHIFlags flags names, dates, MRN, email, phone, ssn", () => {
  const flags = redactPHIFlags(DEMO_DOC.rawText);
  const types = new Set(flags.map((f) => f.type));
  assert.ok(types.has("mrn"), "should flag MRN");
  assert.ok(types.has("email"), "should flag email");
  assert.ok(types.has("phone"), "should flag phone");
  assert.ok(types.has("date"), "should flag date");
  assert.ok(types.has("name"), "should flag name");
  // Flags are sorted by position for deterministic masking.
  for (let i = 1; i < flags.length; i++) {
    assert.ok(flags[i].index >= flags[i - 1].index);
  }
});

test("maskPHI replaces flagged spans without breaking the rest", () => {
  const masked = maskPHI("MRN: DEMO-4471 for alex.demo@example.com");
  assert.match(masked, /\[REDACTED\]/);
  assert.ok(!masked.includes("alex.demo@example.com"));
  assert.ok(!masked.includes("DEMO-4471"));
});

test("buildTimeline sorts ascending and keeps undated events last", () => {
  const sorted = buildTimeline(DEMO_TIMELINE);
  const dated = sorted.filter((e) => !Number.isNaN(Date.parse(e.date)));
  for (let i = 1; i < dated.length; i++) {
    assert.ok(Date.parse(dated[i].date) >= Date.parse(dated[i - 1].date));
  }
  // The unknown-date event is preserved and last.
  assert.equal(sorted.length, DEMO_TIMELINE.length);
  assert.equal(sorted[sorted.length - 1].label, "Family history noted");
});

test("buildTimeline does not mutate input", () => {
  const input = [{ date: "2020-01-01" }, { date: "2019-01-01" }];
  const copy = JSON.parse(JSON.stringify(input));
  buildTimeline(input);
  assert.deepEqual(input, copy);
});

test("appointmentQuestions derives non-empty, deduped questions", () => {
  const qs = appointmentQuestions(DEMO_DOC);
  assert.ok(qs.length >= 4);
  assert.equal(new Set(qs).size, qs.length, "no duplicates");
  assert.ok(qs.some((q) => q.includes("HbA1c")));
  assert.ok(qs.some((q) => /Metformin/i.test(q)));
  // Works with an empty summary too (baseline questions only).
  assert.ok(appointmentQuestions({}).length >= 3);
});

test("medicationQuestions produces question set per medication", () => {
  const qs = medicationQuestions(["Metformin"]);
  assert.ok(qs.some((q) => /what is metformin for/i.test(q)));
  assert.ok(qs.some((q) => /interact/i.test(q)));
  assert.equal(new Set(qs).size, qs.length);
  // Accepts objects with a name field.
  assert.ok(medicationQuestions([{ name: "Aspirin" }]).some((q) => /Aspirin/.test(q)));
});

test("outputDisclosures is FAIL-CLOSED: always has informational+emergency+sources", () => {
  const empty = outputDisclosures({});
  assert.ok(empty.informational && empty.informational.length > 0);
  assert.ok(empty.emergency && empty.emergency.length > 0);
  assert.ok(Array.isArray(empty.sources) && empty.sources.length > 0);
  assert.equal(empty.hasSources, false);
  // Even null input yields the required trio.
  const fromNull = outputDisclosures(null);
  assert.ok(fromNull.informational && fromNull.emergency && fromNull.sources.length);
});

test("outputDisclosures surfaces all required context fields", () => {
  const d = outputDisclosures({ country: "US", sources: ["DEMO glossary"], sourceDate: "2025-09-01" });
  assert.equal(d.hasSources, true);
  assert.deepEqual(d.sources, ["DEMO glossary"]);
  assert.equal(d.applicableCountry, "US");
  assert.match(d.emergency, /911/);
  assert.ok(d.uncertainty.length > 0);
  assert.ok(Array.isArray(d.missingInformation) && d.missingInformation.length > 0);
  assert.ok(d.professionalReview.length > 0);
  assert.equal(d.sourceDate, "2025-09-01");
});

test("safeOutputView always attaches a complete disclosure set", () => {
  const view = safeOutputView({ body: "Some info" });
  assert.ok(view.disclosures.informational);
  assert.ok(view.disclosures.emergency);
  assert.ok(view.disclosures.sources.length > 0);
  assert.equal(view.body, "Some info");
});

test("demo fixtures are clearly labeled and self-consistent", () => {
  assert.equal(DEMO_DOC.demo, true);
  assert.match(DEMO_DOC.label, /DEMO/);
  assert.ok(DEMO_TIMELINE.length > 0);
  assert.ok(DEMO_TERMS.every((t) => t.term && t.plain && t.source));
  assert.ok(ROLES.includes("patient") && ROLES.includes("clinician"));
  assert.ok(CAPABILITIES.length >= 6);
  assert.ok(BLOCKED_CAPABILITIES.includes("diagnosis"));
});
