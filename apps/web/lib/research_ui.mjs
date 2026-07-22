// Pure, dependency-free logic for the Ronin Research notebook UI.
// Kept in plain ESM so it runs under `node --test` with zero install, and is
// imported by the React page (app/research/page.tsx). No DOM, no fetch here.
//
// DEMO-MODE HONESTY: this module never invents citations. `hasFabricatedUrl`
// is a guard the UI uses to REFUSE to render a source that looks made up, and
// `groundingNotice` produces the "based on model knowledge" disclaimer that
// must be shown whenever live retrieval is unavailable.

const DAY_MS = 24 * 60 * 60 * 1000;

/** Number of whole days between an ISO source date and `now`. */
export function ageInDays(sourceDate, now = new Date()) {
  const then = new Date(sourceDate).getTime();
  const ref = now instanceof Date ? now.getTime() : new Date(now).getTime();
  if (Number.isNaN(then) || Number.isNaN(ref)) return null;
  return Math.floor((ref - then) / DAY_MS);
}

/**
 * Freshness descriptor for a source relative to `now`.
 *   fresh  : <= 90 days old
 *   aging  : <= 365 days old
 *   stale  : older than a year
 *   unknown: undatable input
 */
export function freshnessLabel(sourceDate, now = new Date()) {
  const days = ageInDays(sourceDate, now);
  if (days === null || days < 0) {
    return { label: "unknown", tone: "neutral", ageDays: days };
  }
  if (days <= 90) return { label: "fresh", tone: "ok", ageDays: days };
  if (days <= 365) return { label: "aging", tone: "warn", ageDays: days };
  return { label: "stale", tone: "danger", ageDays: days };
}

// Domains we treat as high-trust primary/authoritative sources.
const HIGH_TRUST_SUFFIXES = [".gov", ".edu", ".int", ".mil"];
const HIGH_TRUST_DOMAINS = [
  "nature.com",
  "science.org",
  "arxiv.org",
  "acm.org",
  "ieee.org",
  "nist.gov",
  "who.int",
  "nih.gov",
  "ourworldindata.org",
];
// Domains that are useful but need corroboration (self-published / open-edit).
const LOW_TRUST_DOMAINS = [
  "medium.com",
  "substack.com",
  "wordpress.com",
  "blogspot.com",
  "reddit.com",
  "quora.com",
];

function normalizeDomain(domain = "") {
  return String(domain).trim().toLowerCase().replace(/^www\./, "");
}

/**
 * Classify a source into a trust tier from its domain plus explicit signals.
 * Returns { tier: "high"|"medium"|"low", score, label }.
 * Signals (all optional booleans on `source.signals`): peerReviewed, primary,
 * official, opinion, userGenerated.
 */
export function sourceQuality(source = {}) {
  const domain = normalizeDomain(source.domain);
  const s = source.signals || {};
  let score = 2; // baseline = medium

  const suffixHit = HIGH_TRUST_SUFFIXES.some((suf) => domain.endsWith(suf));
  const domainHit = HIGH_TRUST_DOMAINS.some(
    (d) => domain === d || domain.endsWith("." + d)
  );
  const lowHit = LOW_TRUST_DOMAINS.some(
    (d) => domain === d || domain.endsWith("." + d)
  );

  if (suffixHit || domainHit) score += 1;
  if (lowHit) score -= 1;
  if (s.peerReviewed) score += 1;
  if (s.primary || s.official) score += 1;
  if (s.opinion) score -= 1;
  if (s.userGenerated) score -= 1;

  score = Math.max(0, Math.min(4, score));
  const tier = score >= 3 ? "high" : score <= 1 ? "low" : "medium";
  const label =
    tier === "high"
      ? "High-quality"
      : tier === "low"
        ? "Needs corroboration"
        : "Moderate";
  return { tier, score, label };
}

/**
 * Guard against fabricated / placeholder URLs. Returns true when a URL must
 * NOT be trusted or rendered as a real citation. Realistic https URLs (incl.
 * the reserved example.com family used for demos) pass through as legitimate.
 */
export function hasFabricatedUrl(source = {}) {
  const url = typeof source === "string" ? source : source.url;
  if (!url || typeof url !== "string") return true;
  const trimmed = url.trim();
  if (trimmed === "" || trimmed === "#") return true;
  // Only http(s) is a real citable resource.
  if (!/^https?:\/\//i.test(trimmed)) return true;
  const lower = trimmed.toLowerCase();
  const placeholders = [
    "placeholder",
    "yourdomain",
    "your-domain",
    "example.invalid",
    "example.test",
    "todo",
    "fixme",
    "lorem",
    "ipsum",
    "fake-url",
    "notarealsite",
    "xxxxx",
    "[",
    "]",
    "{",
    "}",
    "<",
    ">",
    "...",
  ];
  if (placeholders.some((p) => lower.includes(p))) return true;
  // A bare host with no dot in the authority is not a real domain.
  const host = lower.replace(/^https?:\/\//, "").split(/[/?#]/)[0];
  if (!host.includes(".")) return true;
  return false;
}

function partitionSupport(claim = {}) {
  const sources = Array.isArray(claim.sources) ? claim.sources : [];
  const supports = sources.filter((r) => (r.stance || "supports") === "supports");
  const refutes = sources.filter((r) => r.stance === "refutes");
  return { sources, supports, refutes };
}

/**
 * Aggregate support strength for a claim from the weight/count of the sources
 * that back it. Returns "strong" | "moderate" | "weak" | "none".
 */
export function supportStrength(claim = {}) {
  const { supports } = partitionSupport(claim);
  if (supports.length === 0) return "none";
  const total = supports.reduce((sum, r) => sum + (Number(r.weight) || 1), 0);
  if (total >= 3 || supports.length >= 3) return "strong";
  if (total >= 1.5 || supports.length >= 2) return "moderate";
  return "weak";
}

/**
 * Determine the evidentiary status of a claim from its attached sources.
 *   supported   : backed by >=1 source, none refuting.
 *   contradicted: at least one source refutes it (conflicting if also supported).
 *   inference   : no sources at all -> must be labelled INFERENCE in the UI.
 * Returns { status, isInference, label, strength, supportCount, refuteCount, conflicting }.
 */
export function claimStatus(claim = {}) {
  const { sources, supports, refutes } = partitionSupport(claim);
  if (sources.length === 0) {
    return {
      status: "inference",
      isInference: true,
      label: "INFERENCE",
      strength: "none",
      supportCount: 0,
      refuteCount: 0,
      conflicting: false,
    };
  }
  if (refutes.length > 0) {
    return {
      status: "contradicted",
      isInference: false,
      label: "CONTRADICTED",
      strength: supportStrength(claim),
      supportCount: supports.length,
      refuteCount: refutes.length,
      conflicting: supports.length > 0,
    };
  }
  return {
    status: "supported",
    isInference: false,
    label: "SUPPORTED",
    strength: supportStrength(claim),
    supportCount: supports.length,
    refuteCount: 0,
    conflicting: false,
  };
}

/**
 * Percentage of claims backed by at least one source (of any stance).
 * Returns { covered, total, percent } with percent rounded to an integer.
 */
export function citationCoverage(claims = []) {
  const total = claims.length;
  if (total === 0) return { covered: 0, total: 0, percent: 0 };
  const covered = claims.filter(
    (c) => Array.isArray(c.sources) && c.sources.length > 0
  ).length;
  return { covered, total, percent: Math.round((covered / total) * 100) };
}

/**
 * Heuristic pairwise contradiction detector for two claims. Two claims conflict
 * when they concern the same `topic` but assert opposing `polarity`
 * ("affirms" vs "denies"). Returns { contradiction, topic, reason }.
 */
export function detectContradiction(claimA = {}, claimB = {}) {
  const topicA = (claimA.topic || "").trim().toLowerCase();
  const topicB = (claimB.topic || "").trim().toLowerCase();
  if (!topicA || !topicB || topicA !== topicB) {
    return { contradiction: false, topic: null, reason: "different topics" };
  }
  const polA = claimA.polarity;
  const polB = claimB.polarity;
  if (polA && polB && polA !== polB) {
    return {
      contradiction: true,
      topic: topicA,
      reason: `same topic asserted "${polA}" vs "${polB}"`,
    };
  }
  return { contradiction: false, topic: topicA, reason: "same stance" };
}

/**
 * The honesty disclaimer. When live retrieval is unavailable, the UI MUST show
 * that any answer rests on the model's training knowledge, not current sources.
 * Returns { grounded, tone, title, notice }.
 */
export function groundingNotice(retrievalAvailable) {
  if (retrievalAvailable) {
    return {
      grounded: true,
      tone: "ok",
      title: "Grounded in retrieved sources",
      notice:
        "Answers below cite live sources collected during this session. Verify each citation before relying on it.",
    };
  }
  return {
    grounded: false,
    tone: "warn",
    title: "Model knowledge only — no live sources",
    notice:
      "Retrieval is unavailable, so this notebook is running on demo fixtures. Any answer here reflects the model's training knowledge, not current sources, and must not be presented as freshly cited fact.",
  };
}

// ---------------------------------------------------------------------------
// Demo fixtures. Clearly labelled as DEMO. Every URL is a realistic https URL
// on a reserved example domain — none are fabricated-looking, and all pass
// `hasFabricatedUrl`.
// ---------------------------------------------------------------------------

/** DEMO search plan: staged queries with rationale. */
export const DEMO_QUERIES = [
  {
    id: "q1",
    stage: "seed",
    query: "grid-scale battery storage cost per kWh 2025",
    rationale: "Establish the current baseline figure and its primary source.",
    status: "done",
  },
  {
    id: "q2",
    stage: "expand",
    query: "lithium iron phosphate vs sodium-ion grid storage lifetime",
    rationale: "Compare competing chemistries to test durability claims.",
    status: "done",
  },
  {
    id: "q3",
    stage: "verify",
    query: "IEA 2025 battery storage deployment gigawatt figures dataset",
    rationale: "Cross-check deployment numbers against a second authority.",
    status: "running",
  },
];

/** DEMO sources collected + rejected. Rejected ones carry a `rejectedReason`. */
export const DEMO_SOURCES = [
  {
    id: "s1",
    title: "Grid-Scale Energy Storage: 2025 Cost Review",
    domain: "nist.gov",
    url: "https://www.nist.gov/energy/storage-cost-review-2025",
    date: "2026-05-10",
    signals: { primary: true, official: true },
    accepted: true,
  },
  {
    id: "s2",
    title: "Long-duration battery chemistries compared",
    domain: "nature.com",
    url: "https://www.nature.com/articles/example-longduration-2025",
    date: "2025-11-02",
    signals: { peerReviewed: true },
    accepted: true,
  },
  {
    id: "s3",
    title: "IEA battery deployment tracker (open dataset)",
    domain: "ourworldindata.org",
    url: "https://ourworldindata.org/grid-battery-deployment",
    date: "2026-06-30",
    signals: { primary: true },
    accepted: true,
  },
  {
    id: "s4",
    title: "Why sodium-ion already beats lithium everywhere",
    domain: "medium.com",
    url: "https://medium.com/@analyst/sodium-ion-beats-lithium-9f2c",
    date: "2024-01-15",
    signals: { opinion: true, userGenerated: true },
    accepted: false,
    rejectedReason:
      "Opinion post on a self-publishing platform, no primary data, and over 18 months old.",
  },
  {
    id: "s5",
    title: "Battery storage — encyclopedia overview",
    domain: "en.wikipedia.org",
    url: "https://en.wikipedia.org/wiki/Grid_energy_storage",
    date: "2026-07-01",
    signals: { userGenerated: true },
    accepted: false,
    rejectedReason:
      "Tertiary source; used only to find primary references, not cited directly.",
  },
];

/**
 * DEMO claims mapped to sources. Stances: "supports" | "refutes".
 * One claim has no sources (INFERENCE), one is contradicted by conflicting
 * evidence, illustrating the sourced-fact vs. model-inference separation.
 */
export const DEMO_CLAIMS = [
  {
    id: "c1",
    text: "Average grid-scale battery storage cost fell below $150/kWh in 2025.",
    topic: "grid battery cost 2025",
    polarity: "affirms",
    sources: [
      { sourceId: "s1", stance: "supports", weight: 2 },
      { sourceId: "s3", stance: "supports", weight: 1 },
    ],
  },
  {
    id: "c2",
    text: "Sodium-ion cells now exceed lithium iron phosphate cycle life at grid scale.",
    topic: "sodium-ion vs lfp lifetime",
    polarity: "affirms",
    sources: [
      { sourceId: "s2", stance: "refutes", weight: 2 },
      { sourceId: "s4", stance: "supports", weight: 1 },
    ],
  },
  {
    id: "c3",
    text: "Peer-reviewed data shows LFP retains higher cycle life than sodium-ion today.",
    topic: "sodium-ion vs lfp lifetime",
    polarity: "denies",
    sources: [{ sourceId: "s2", stance: "supports", weight: 2 }],
  },
  {
    id: "c4",
    text: "Storage cost declines will likely continue at a similar pace through 2030.",
    topic: "cost trajectory 2030",
    polarity: "affirms",
    sources: [], // No source -> INFERENCE, must be labelled as model reasoning.
  },
];
