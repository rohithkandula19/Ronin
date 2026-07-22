// Ronin Forge logic tests — run with `node --test` (Node 22, no install).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  jobStateBadge,
  canTransition,
  bundleIsTrained,
  canRelease,
  releaseBlockReason,
  datasetEligibility,
  splitCounts,
  contaminationStatus,
  evalDelta,
  overageCost,
  DEMO_DATASET,
  DEMO_JOB,
  DEMO_ADAPTERS,
  DEMO_EVALS,
} from "./forge.mjs";

test("jobStateBadge maps known states and marks terminals", () => {
  assert.equal(jobStateBadge("generated").tone, "neutral");
  assert.equal(jobStateBadge("generated").isTerminal, false);
  assert.equal(jobStateBadge("failed").tone, "danger");
  assert.equal(jobStateBadge("failed").isTerminal, true);
  assert.equal(jobStateBadge("released").tone, "ok");
  assert.match(jobStateBadge("generated").label, /not trained/i);
});

test("jobStateBadge returns neutral Unknown for bogus states", () => {
  const b = jobStateBadge("banana");
  assert.equal(b.tone, "neutral");
  assert.equal(b.label, "Unknown");
  assert.equal(b.isTerminal, false);
});

test("canTransition enforces the lifecycle and forbids skipping", () => {
  assert.equal(canTransition("generated", "submitted"), true);
  assert.equal(canTransition("approved", "released"), true);
  // The headline rule: you cannot jump straight from generated to released.
  assert.equal(canTransition("generated", "released"), false);
  assert.equal(canTransition("completed", "released"), false);
  assert.equal(canTransition("failed", "running"), false);
});

test("bundleIsTrained: generated/submitted/running/failed are NOT trained", () => {
  assert.equal(bundleIsTrained({ state: "generated" }), false);
  assert.equal(bundleIsTrained({ state: "submitted" }), false);
  assert.equal(bundleIsTrained({ state: "running" }), false);
  assert.equal(bundleIsTrained({ state: "failed" }), false);
  assert.equal(bundleIsTrained(null), false);
  assert.equal(bundleIsTrained({ state: "banana" }), false);
});

test("bundleIsTrained: completed and released ARE trained", () => {
  assert.equal(bundleIsTrained({ state: "completed" }), true);
  assert.equal(bundleIsTrained({ state: "released" }), true);
  assert.equal(bundleIsTrained({ state: "evaluating" }), true);
});

test("canRelease is false for a generated adapter (no state-skipping)", () => {
  const generated = DEMO_ADAPTERS.find((a) => a.id === "healthcare-en-us-v1");
  assert.equal(canRelease(generated), false);
  assert.match(releaseBlockReason(generated), /approved/i);
});

test("canRelease requires approved state + evals + a human approver", () => {
  const base = {
    state: "approved",
    evalResults: [{ suite: "safety", passed: 50, total: 50 }],
    approver: { name: "A. Human", type: "human" },
  };
  assert.equal(canRelease(base), true);
  // Missing evals -> blocked.
  assert.equal(canRelease({ ...base, evalResults: [] }), false);
  // Non-human approver -> blocked.
  assert.equal(canRelease({ ...base, approver: { name: "autobot", type: "automated" } }), false);
  // No approver -> blocked.
  assert.equal(canRelease({ ...base, approver: null }), false);
  // Wrong state (evaluating) -> blocked even with everything else present.
  assert.equal(canRelease({ ...base, state: "evaluating" }), false);
});

test("datasetEligibility fails closed on missing consent or license", () => {
  const good = { license: "MIT", consent: "obtained", qualityScore: 0.9 };
  assert.equal(datasetEligibility(good).eligible, true);
  assert.equal(datasetEligibility({ license: "unknown", consent: true }).eligible, false);
  assert.equal(datasetEligibility({ license: "MIT", consent: false }).eligible, false);
  assert.equal(datasetEligibility({}).eligible, false);
  assert.equal(datasetEligibility(null).eligible, false);
});

test("datasetEligibility rejects failed redaction, low quality, contamination", () => {
  assert.match(
    datasetEligibility({ license: "MIT", consent: true, redaction: { status: "failed" } }).reason,
    /redaction/i
  );
  assert.match(
    datasetEligibility({ license: "MIT", consent: true, qualityScore: 0.3 }).reason,
    /quality/i
  );
  assert.match(
    datasetEligibility({ license: "MIT", consent: true, contaminated: true }).reason,
    /contamination/i
  );
});

test("splitCounts sums exactly to n and validates inputs", () => {
  assert.deepEqual(splitCounts(1000), { train: 800, val: 100, test: 100 });
  const s = splitCounts(1001);
  assert.equal(s.train + s.val + s.test, 1001);
  assert.deepEqual(splitCounts(10, { train: 0.6, val: 0.2, test: 0.2 }), { train: 6, val: 2, test: 2 });
  assert.throws(() => splitCounts(-1), /non-negative/);
  assert.throws(() => splitCounts(10, { train: 0.5, val: 0.2, test: 0.2 }), /sum to 1/);
});

test("contaminationStatus flags overlaps / high similarity, else clean", () => {
  assert.equal(contaminationStatus(DEMO_DATASET.contaminationPairs).status, "clean");
  assert.equal(contaminationStatus([{ similarity: 0.95 }]).status, "contaminated");
  assert.equal(contaminationStatus([{ overlap: true }]).status, "contaminated");
  assert.equal(contaminationStatus("nope").status, "unknown");
});

test("evalDelta reports improved / regressed / same / new per suite", () => {
  const d = evalDelta(DEMO_EVALS.current, DEMO_EVALS.previous);
  assert.equal(d.task.direction, "improved"); // 0.91 > 0.85
  assert.equal(d.safety.direction, "regressed"); // 0.96 < 1.0
  assert.equal(d.toxicity.direction, "improved"); // 1.0 > 0.99
  const withNew = evalDelta({ extra: 0.5 }, {});
  assert.equal(withNew.extra.direction, "new");
});

test("overageCost throws on unknown price (never assumes free)", () => {
  const prices = { tokens: 0.000002, gpuHours: 2.5 };
  const { total, lines } = overageCost({ tokens: 1_000_000, gpuHours: 4 }, prices);
  assert.equal(lines.length, 2);
  assert.ok(Math.abs(total - (2 + 10)) < 1e-9);
  assert.throws(() => overageCost({ mysteryMetric: 5 }, prices), /no price/);
  assert.throws(() => overageCost(null, prices), /required/);
});

test("demo fixtures are labelled and internally honest", () => {
  assert.equal(DEMO_JOB.demo, true);
  // The default demo job is generated => not a trained model.
  assert.equal(DEMO_JOB.state, "generated");
  assert.equal(bundleIsTrained(DEMO_JOB), false);
  assert.equal(DEMO_JOB.hardware.status, "BLOCKED_HARDWARE");
  // The generated demo adapter is not releasable.
  const gen = DEMO_ADAPTERS.find((a) => a.state === "generated");
  assert.equal(canRelease(gen), false);
  // At least one demo dataset row is ineligible, proving the gate bites.
  const ineligible = DEMO_DATASET.items.filter((i) => !datasetEligibility(i).eligible);
  assert.ok(ineligible.length >= 1);
});
