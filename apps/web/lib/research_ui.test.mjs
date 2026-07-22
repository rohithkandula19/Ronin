// Frontend logic tests for the Research notebook — run with `node --test`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ageInDays,
  freshnessLabel,
  sourceQuality,
  hasFabricatedUrl,
  supportStrength,
  claimStatus,
  citationCoverage,
  detectContradiction,
  groundingNotice,
  DEMO_SOURCES,
  DEMO_CLAIMS,
  DEMO_QUERIES,
} from "./research_ui.mjs";

const NOW = new Date("2026-07-22T00:00:00Z");

test("ageInDays computes whole-day gaps and rejects junk", () => {
  assert.equal(ageInDays("2026-07-12T00:00:00Z", NOW), 10);
  assert.equal(ageInDays("not-a-date", NOW), null);
});

test("freshnessLabel buckets by age (fresh/aging/stale/unknown)", () => {
  assert.equal(freshnessLabel("2026-06-01", NOW).label, "fresh");
  assert.equal(freshnessLabel("2026-01-01", NOW).label, "aging");
  assert.equal(freshnessLabel("2023-01-01", NOW).label, "stale");
  assert.equal(freshnessLabel("bogus", NOW).label, "unknown");
});

test("sourceQuality rates .gov/official as high and blogs as low", () => {
  assert.equal(sourceQuality({ domain: "nist.gov", signals: { official: true } }).tier, "high");
  assert.equal(sourceQuality({ domain: "nature.com", signals: { peerReviewed: true } }).tier, "high");
  assert.equal(
    sourceQuality({ domain: "medium.com", signals: { opinion: true, userGenerated: true } }).tier,
    "low"
  );
  assert.equal(sourceQuality({ domain: "acme.io" }).tier, "medium");
});

test("hasFabricatedUrl passes realistic https URLs including example.com", () => {
  assert.equal(hasFabricatedUrl({ url: "https://www.nist.gov/x" }), false);
  assert.equal(hasFabricatedUrl({ url: "https://example.com/report" }), false);
});

test("hasFabricatedUrl flags placeholders, non-http, and hostless URLs", () => {
  assert.equal(hasFabricatedUrl({ url: "" }), true);
  assert.equal(hasFabricatedUrl({ url: "#" }), true);
  assert.equal(hasFabricatedUrl({ url: "https://yourdomain.com/PLACEHOLDER" }), true);
  assert.equal(hasFabricatedUrl({ url: "ftp://files.example.com/x" }), true);
  assert.equal(hasFabricatedUrl({ url: "javascript:alert(1)" }), true);
  assert.equal(hasFabricatedUrl({ url: "https://localhost/foo" }), true);
  assert.equal(hasFabricatedUrl({}), true);
});

test("supportStrength scales with weighted supporting sources", () => {
  assert.equal(supportStrength({ sources: [] }), "none");
  assert.equal(supportStrength({ sources: [{ stance: "supports", weight: 1 }] }), "weak");
  assert.equal(
    supportStrength({ sources: [{ stance: "supports", weight: 1 }, { stance: "supports", weight: 1 }] }),
    "moderate"
  );
  assert.equal(supportStrength({ sources: [{ stance: "supports", weight: 4 }] }), "strong");
});

test("claimStatus labels a sourceless claim as INFERENCE", () => {
  const st = claimStatus({ sources: [] });
  assert.equal(st.status, "inference");
  assert.equal(st.isInference, true);
  assert.equal(st.label, "INFERENCE");
});

test("claimStatus marks supported and contradicted claims", () => {
  const supported = claimStatus({ sources: [{ sourceId: "s1", stance: "supports", weight: 2 }] });
  assert.equal(supported.status, "supported");

  const conflicting = claimStatus({
    sources: [
      { sourceId: "s1", stance: "supports" },
      { sourceId: "s2", stance: "refutes" },
    ],
  });
  assert.equal(conflicting.status, "contradicted");
  assert.equal(conflicting.conflicting, true);
});

test("citationCoverage reports percent with >=1 source", () => {
  const cov = citationCoverage([
    { sources: [{ sourceId: "s1" }] },
    { sources: [] },
    { sources: [{ sourceId: "s2" }] },
    { sources: [{ sourceId: "s3" }] },
  ]);
  assert.equal(cov.covered, 3);
  assert.equal(cov.total, 4);
  assert.equal(cov.percent, 75);
  assert.equal(citationCoverage([]).percent, 0);
});

test("detectContradiction fires on same topic, opposing polarity", () => {
  const a = { topic: "sodium vs lfp", polarity: "affirms" };
  const b = { topic: "sodium vs lfp", polarity: "denies" };
  const c = { topic: "grid cost", polarity: "affirms" };
  assert.equal(detectContradiction(a, b).contradiction, true);
  assert.equal(detectContradiction(a, c).contradiction, false);
  assert.equal(detectContradiction(a, { topic: "sodium vs lfp", polarity: "affirms" }).contradiction, false);
});

test("groundingNotice is honest when retrieval is unavailable", () => {
  const off = groundingNotice(false);
  assert.equal(off.grounded, false);
  assert.match(off.notice, /model's training knowledge/i);
  assert.equal(groundingNotice(true).grounded, true);
});

test("demo fixtures are shaped correctly and never fabricated", () => {
  assert.ok(DEMO_SOURCES.length >= 3);
  assert.ok(DEMO_CLAIMS.length >= 3);
  assert.ok(DEMO_QUERIES.length >= 3);
  for (const s of DEMO_SOURCES) {
    assert.equal(hasFabricatedUrl(s), false, `${s.url} should not look fabricated`);
    assert.ok(s.url.startsWith("https://"));
  }
  // At least one INFERENCE claim and one contradicted claim exist in the demo.
  const statuses = DEMO_CLAIMS.map((c) => claimStatus(c).status);
  assert.ok(statuses.includes("inference"));
  assert.ok(statuses.includes("contradicted"));
});

test("demo contradiction pair is detectable", () => {
  const c2 = DEMO_CLAIMS.find((c) => c.id === "c2");
  const c3 = DEMO_CLAIMS.find((c) => c.id === "c3");
  assert.equal(detectContradiction(c2, c3).contradiction, true);
});
