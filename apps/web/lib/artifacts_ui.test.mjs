// Artifacts UI logic tests — run with `node --test` (no install required).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEMO_ARTIFACTS,
  TYPES,
  canRestore,
  latestVersion,
  provenanceLinks,
  typeBadge,
  versionDiffSummary,
} from "./artifacts_ui.mjs";

test("typeBadge maps every canonical type to a label + tone + glyph", () => {
  for (const t of TYPES) {
    const b = typeBadge(t);
    assert.ok(b.label && b.tone && b.glyph, `${t} has badge`);
  }
  assert.equal(typeBadge("medical_summary").tone, "danger");
  assert.equal(typeBadge("bogus").label, "Unknown");
});

test("latestVersion picks the highest-numbered version", () => {
  const report = DEMO_ARTIFACTS.find((a) => a.id === "art_q2_report");
  assert.equal(latestVersion(report).id, "v2");
  assert.equal(latestVersion({ versions: [] }), null);
  assert.equal(latestVersion(null), null);
});

test("latestVersion does not mutate the artifact's versions array", () => {
  const report = DEMO_ARTIFACTS.find((a) => a.id === "art_q2_report");
  const order = report.versions.map((v) => v.id).join(",");
  latestVersion(report);
  assert.equal(report.versions.map((v) => v.id).join(","), order);
});

test("canRestore allows restoring to an older, existing version", () => {
  const report = DEMO_ARTIFACTS.find((a) => a.id === "art_q2_report");
  assert.equal(canRestore(report, "v1"), true);
});

test("canRestore refuses the current/latest version (no-op)", () => {
  const report = DEMO_ARTIFACTS.find((a) => a.id === "art_q2_report");
  assert.equal(canRestore(report, "v2"), false);
});

test("canRestore fails closed for missing versions and locked artifacts", () => {
  const report = DEMO_ARTIFACTS.find((a) => a.id === "art_q2_report");
  assert.equal(canRestore(report, "nope"), false);
  const med = DEMO_ARTIFACTS.find((a) => a.id === "art_med_summary");
  assert.equal(med.locked, true);
  assert.equal(canRestore(med, "v1"), false);
  assert.equal(canRestore(null, "v1"), false);
});

test("versionDiffSummary detects added, removed, and changed blocks", () => {
  const report = DEMO_ARTIFACTS.find((a) => a.id === "art_q2_report");
  const [v1, v2] = report.versions;
  const diff = versionDiffSummary(v1, v2);
  assert.ok(diff.added.includes("next_steps"));
  assert.ok(diff.changed.includes("summary"));
  assert.equal(diff.removed.length, 0);
  assert.ok(diff.unchanged.includes("title"));
  assert.equal(diff.total, 2);
});

test("versionDiffSummary reports no changes for identical structure", () => {
  const v = { content: [{ key: "a", value: 1 }] };
  const diff = versionDiffSummary(v, { content: [{ key: "a", value: 1 }] });
  assert.equal(diff.total, 0);
  assert.equal(diff.summary, "No changes");
});

test("versionDiffSummary compares structurally (arrays), not as HTML", () => {
  const arch = DEMO_ARTIFACTS.find((a) => a.id === "art_med_summary");
  const [v1, v2] = arch.versions;
  const diff = versionDiffSummary(v1, v2);
  assert.ok(diff.changed.includes("topics"));
  assert.ok(diff.unchanged.includes("disclaimer"));
});

test("provenanceLinks returns a stable shape with conversation/sources/model/adapter/approvals", () => {
  const report = DEMO_ARTIFACTS.find((a) => a.id === "art_q2_report");
  const p = provenanceLinks(report);
  assert.equal(p.conversation.id, "conv_412");
  assert.equal(p.model, "claude-opus-4-8");
  assert.equal(p.sources.length, 2);
  assert.equal(p.approvals.length, 1);
});

test("provenanceLinks never fabricates references for a bare artifact", () => {
  const p = provenanceLinks({});
  assert.equal(p.conversation, null);
  assert.equal(p.model, null);
  assert.equal(p.adapter, null);
  assert.deepEqual(p.sources, []);
  assert.deepEqual(p.approvals, []);
});
