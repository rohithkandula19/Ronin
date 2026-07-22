// Pure, dependency-free logic for the Artifacts UI.
// Plain ESM so it runs under `node --test` with zero install, and is imported
// by the React page (app/artifacts/page.tsx). No DOM, no fetch, no React here.
//
// This module is the testable core. The page renders from the DEMO_* fixtures
// exported below (clearly labeled demo data); live data comes from /api/v1 at
// runtime. Artifacts are stored as STRUCTURED CONTENT (blocks/fields), not raw
// HTML — the diff and render logic operate on that structure.

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Canonical artifact types. */
export const TYPES = [
  "document",
  "report",
  "code",
  "diff",
  "plan",
  "table",
  "dataset",
  "diagram",
  "timeline",
  "checklist",
  "study_plan",
  "medical_summary",
  "research_notebook",
  "architecture",
  "workflow",
];

const TYPE_META = {
  document: { label: "Document", tone: "neutral", glyph: "☷" },
  report: { label: "Report", tone: "info", glyph: "☷" },
  code: { label: "Code", tone: "info", glyph: "‹›" },
  diff: { label: "Diff", tone: "warn", glyph: "±" },
  plan: { label: "Plan", tone: "info", glyph: "☰" },
  table: { label: "Table", tone: "neutral", glyph: "▦" },
  dataset: { label: "Dataset", tone: "neutral", glyph: "▤" },
  diagram: { label: "Diagram", tone: "info", glyph: "◈" },
  timeline: { label: "Timeline", tone: "info", glyph: "─" },
  checklist: { label: "Checklist", tone: "ok", glyph: "☑" },
  study_plan: { label: "Study plan", tone: "info", glyph: "☰" },
  medical_summary: { label: "Medical summary", tone: "danger", glyph: "✚" },
  research_notebook: { label: "Research notebook", tone: "info", glyph: "✎" },
  architecture: { label: "Architecture", tone: "info", glyph: "□" },
  workflow: { label: "Workflow", tone: "info", glyph: "→" },
};

/** Badge descriptor for an artifact type; unknown → neutral fallback. */
export function typeBadge(type) {
  return TYPE_META[type] || { label: "Unknown", tone: "neutral", glyph: "○" };
}

// ---------------------------------------------------------------------------
// Versions
// ---------------------------------------------------------------------------

/**
 * The latest (highest-numbered) version of an artifact, or null if none.
 * Never mutates. Falls back to array order if `number` is absent.
 */
export function latestVersion(artifact) {
  const versions = artifact && Array.isArray(artifact.versions) ? artifact.versions : [];
  if (versions.length === 0) return null;
  return versions.reduce((best, v) => {
    if (!best) return v;
    const bn = Number(best.number ?? -Infinity);
    const vn = Number(v.number ?? -Infinity);
    return vn >= bn ? v : best;
  }, null);
}

/**
 * Can we restore this artifact to versionId?
 * Fail-closed: the version must exist AND not already be the current/latest
 * one (restoring to the current version is a no-op we disallow). Locked
 * artifacts (e.g. signed/approved medical summaries) cannot be restored.
 */
export function canRestore(artifact, versionId) {
  if (!artifact || !Array.isArray(artifact.versions)) return false;
  if (artifact.locked === true) return false;
  const target = artifact.versions.find((v) => v.id === versionId);
  if (!target) return false;
  const latest = latestVersion(artifact);
  if (latest && latest.id === versionId) return false;
  return true;
}

/**
 * Summarize the diff between two structured versions.
 * Versions carry `content` as an array of blocks: { key, value }.
 * Returns counts of added/removed/changed blocks plus per-field details.
 * Pure structural comparison — no HTML parsing. Never mutates.
 */
export function versionDiffSummary(v1, v2) {
  const a = blockMap(v1);
  const b = blockMap(v2);
  const added = [];
  const removed = [];
  const changed = [];
  for (const key of Object.keys(b)) {
    if (!(key in a)) added.push(key);
    else if (!deepEqualish(a[key], b[key])) changed.push(key);
  }
  for (const key of Object.keys(a)) {
    if (!(key in b)) removed.push(key);
  }
  return {
    added,
    removed,
    changed,
    unchanged: Object.keys(a).filter((k) => k in b && deepEqualish(a[k], b[k])),
    total: added.length + removed.length + changed.length,
    summary:
      added.length + removed.length + changed.length === 0
        ? "No changes"
        : `${added.length} added, ${removed.length} removed, ${changed.length} changed`,
  };
}

function blockMap(version) {
  const blocks = version && Array.isArray(version.content) ? version.content : [];
  const map = {};
  for (const block of blocks) {
    if (block && block.key != null) map[block.key] = block.value;
  }
  return map;
}

function deepEqualish(x, y) {
  if (x === y) return true;
  return JSON.stringify(x) === JSON.stringify(y);
}

// ---------------------------------------------------------------------------
// Provenance
// ---------------------------------------------------------------------------

/**
 * Provenance links for an artifact: conversation, sources, model, adapter,
 * and approvals. Always returns a stable shape (empty arrays/nulls when
 * absent) so the UI can render honestly without fabricating references.
 */
export function provenanceLinks(artifact) {
  const a = artifact || {};
  return {
    conversation: a.conversation_id ? { id: a.conversation_id, href: `/conversations/${a.conversation_id}` } : null,
    sources: Array.isArray(a.sources) ? a.sources : [],
    model: a.model || null,
    adapter: a.adapter || null,
    approvals: Array.isArray(a.approvals) ? a.approvals : [],
  };
}

// ---------------------------------------------------------------------------
// Demo fixtures (clearly labeled — NOT live data)
// ---------------------------------------------------------------------------

export const DEMO_ARTIFACTS = [
  {
    id: "art_q2_report",
    title: "Q2 revenue report",
    type: "report",
    conversation_id: "conv_412",
    sources: [
      { label: "Stripe export (read-only)", href: "#" },
      { label: "Linear issues", href: "#" },
    ],
    model: "claude-opus-4-8",
    adapter: null,
    approvals: [{ by: "you@example.com", at: "2026-07-15T11:02:00Z", note: "Reviewed figures" }],
    locked: false,
    versions: [
      {
        id: "v1",
        number: 1,
        created_at: "2026-07-15T11:00:00Z",
        author: "Ronin",
        content: [
          { key: "title", value: "Q2 revenue report" },
          { key: "mrr", value: 4230 },
          { key: "summary", value: "Steady growth; one Pro churn." },
        ],
      },
      {
        id: "v2",
        number: 2,
        created_at: "2026-07-15T11:02:00Z",
        author: "you@example.com",
        content: [
          { key: "title", value: "Q2 revenue report" },
          { key: "mrr", value: 4230 },
          { key: "summary", value: "Steady growth; one Pro churn. Added churn analysis." },
          { key: "next_steps", value: "Exit interviews with churned users." },
        ],
      },
    ],
  },
  {
    id: "art_arch",
    title: "Service architecture",
    type: "architecture",
    conversation_id: "conv_388",
    sources: [{ label: "Repository README", href: "#" }],
    model: "claude-opus-4-8",
    adapter: "coding-agent-v2",
    approvals: [],
    locked: false,
    versions: [
      {
        id: "v1",
        number: 1,
        created_at: "2026-07-10T09:00:00Z",
        author: "Ronin",
        content: [
          { key: "nodes", value: ["web", "api", "db"] },
          { key: "notes", value: "Monolith API + Postgres." },
        ],
      },
    ],
  },
  {
    id: "art_med_summary",
    title: "Visit summary (educational)",
    type: "medical_summary",
    conversation_id: "conv_501",
    sources: [{ label: "Session notes", href: "#" }],
    model: "claude-opus-4-8",
    adapter: "healthcare-en-us-v1",
    approvals: [{ by: "clinician", at: "2026-07-12T15:00:00Z", note: "Signed off" }],
    // Signed/approved medical summaries are locked against restore.
    locked: true,
    versions: [
      {
        id: "v1",
        number: 1,
        created_at: "2026-07-12T14:00:00Z",
        author: "Ronin",
        content: [
          { key: "disclaimer", value: "Educational information, not medical advice." },
          { key: "topics", value: ["allergy management"] },
        ],
      },
      {
        id: "v2",
        number: 2,
        created_at: "2026-07-12T15:00:00Z",
        author: "clinician",
        content: [
          { key: "disclaimer", value: "Educational information, not medical advice." },
          { key: "topics", value: ["allergy management", "emergency plan"] },
        ],
      },
    ],
  },
];
