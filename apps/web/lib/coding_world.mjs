// Pure, dependency-free logic for the Coding World workspace UI.
// Plain ESM so it runs under `node --test` with zero install, and is imported
// by the React page (app/coding/page.tsx). No DOM, no fetch, no React here.
//
// This module is the testable core. The page renders from the DEMO_* fixtures
// exported below; live data comes from /api/v1 at runtime.

// ---------------------------------------------------------------------------
// Plan tracker
// ---------------------------------------------------------------------------

/** Canonical plan-item statuses and the glyph each renders with. */
export const PLAN_STATUS = {
  done: { glyph: "✓", label: "Done", tone: "ok" }, //        ✓
  in_progress: { glyph: "▶", label: "In progress", tone: "info" }, // ▶
  pending: { glyph: "☐", label: "Pending", tone: "neutral" }, //     ☐
  skipped: { glyph: "⊘", label: "Skipped", tone: "warn" }, //       ⊘
  failed: { glyph: "✗", label: "Failed", tone: "danger" }, //       ✗
};

/** Glyph for a plan-item status; unknown statuses fall back to the pending box. */
export function planGlyph(status) {
  return (PLAN_STATUS[status] || PLAN_STATUS.pending).glyph;
}

/**
 * Summarize a plan/todo list: count by status and a completion percentage.
 * Percent counts only `done` items over the total; empty list → 0%.
 * Never mutates input.
 */
export function planProgress(todos) {
  const list = Array.isArray(todos) ? todos : [];
  const counts = { done: 0, in_progress: 0, pending: 0, skipped: 0, failed: 0 };
  for (const t of list) {
    const s = t && t.status;
    if (Object.prototype.hasOwnProperty.call(counts, s)) counts[s] += 1;
  }
  const total = list.length;
  const percent = total === 0 ? 0 : Math.round((counts.done / total) * 100);
  return { total, counts, percent };
}

// ---------------------------------------------------------------------------
// Tool risk tiers
// ---------------------------------------------------------------------------

/** Risk tier metadata. Ordered least → most dangerous. */
export const RISK_TIERS = {
  read_only: { label: "Read-only", tone: "ok", rank: 0 },
  reversible: { label: "Reversible", tone: "warn", rank: 1 },
  destructive: { label: "Destructive", tone: "danger", rank: 2 },
  unknown: { label: "Unknown", tone: "neutral", rank: 3 },
};

const READ_ONLY_TOOLS = new Set(["Read", "Glob", "Grep", "List", "WebFetch", "WebSearch"]);
const REVERSIBLE_TOOLS = new Set(["Write", "Edit", "NotebookEdit", "CreateFile", "MultiEdit"]);
const DESTRUCTIVE_TOOLS = new Set(["Bash", "Delete", "Remove", "Exec", "Deploy", "GitPush", "Publish"]);

/**
 * Map a tool name to a risk tier descriptor. Fail-closed: an unknown tool is
 * treated as `unknown` (not read-only), so callers never assume safety.
 */
export function riskForTool(tool) {
  const name = typeof tool === "string" ? tool.trim() : "";
  let tier = "unknown";
  if (READ_ONLY_TOOLS.has(name)) tier = "read_only";
  else if (REVERSIBLE_TOOLS.has(name)) tier = "reversible";
  else if (DESTRUCTIVE_TOOLS.has(name)) tier = "destructive";
  return { tier, ...RISK_TIERS[tier] };
}

// ---------------------------------------------------------------------------
// Diff summary
// ---------------------------------------------------------------------------

/**
 * Summarize a set of changed files into totals and a per-file breakdown.
 * Each file is expected to look like { path, added, removed, status }.
 * Missing counts are treated as 0. Never mutates input.
 */
export function summarizeDiff(files) {
  const list = Array.isArray(files) ? files : [];
  let added = 0;
  let removed = 0;
  const perFile = list.map((f) => {
    const a = Number.isFinite(f && f.added) ? f.added : 0;
    const r = Number.isFinite(f && f.removed) ? f.removed : 0;
    added += a;
    removed += r;
    return {
      path: (f && f.path) || "(unknown)",
      added: a,
      removed: r,
      status: (f && f.status) || "modified",
      net: a - r,
    };
  });
  return { files: perFile.length, added, removed, net: added - removed, perFile };
}

// ---------------------------------------------------------------------------
// Tool-call formatting
// ---------------------------------------------------------------------------

/**
 * Render a tool call as the transcript string `⏺ name(arg)`.
 * A nullish/empty arg renders as `⏺ name()`. Non-string args are stringified.
 */
export function formatToolCall(name, arg) {
  const n = typeof name === "string" && name.trim() ? name.trim() : "tool";
  let a = "";
  if (arg !== undefined && arg !== null && arg !== "") {
    a = typeof arg === "string" ? arg : String(arg);
  }
  return `⏺ ${n}(${a})`; // ⏺
}

/** Render a tool result line as `⎿ text`. */
export function formatToolResult(text) {
  const t = text === undefined || text === null ? "" : String(text);
  return `⎿ ${t}`; // ⎿
}

// ---------------------------------------------------------------------------
// Approvals (fail-closed safety floor)
// ---------------------------------------------------------------------------

// Actions that are safe to auto-run without a human approval. Everything else,
// including anything unrecognized, requires approval.
const AUTO_APPROVE_ACTIONS = new Set(["read_file", "search", "list_files", "web_fetch", "get_status"]);

/**
 * Decide whether an action needs human approval. Fail-closed: unknown or
 * malformed actions ALWAYS require approval. Mirrors the real safety floor —
 * web actions do not get a pass.
 *
 * Accepts either a string action name or an object { type, risk }.
 * A `destructive` risk always requires approval regardless of type.
 */
export function isApprovalRequired(action) {
  if (typeof action === "string") {
    return !AUTO_APPROVE_ACTIONS.has(action.trim());
  }
  if (action && typeof action === "object") {
    if (action.risk === "destructive") return true;
    const type = typeof action.type === "string" ? action.type.trim() : "";
    if (!type) return true; // malformed → fail closed
    return !AUTO_APPROVE_ACTIONS.has(type);
  }
  return true; // null/undefined/number/etc → fail closed
}

// ---------------------------------------------------------------------------
// Cost / token accounting
// ---------------------------------------------------------------------------

/**
 * Compute a cost readout line from token counts and a price table.
 * priceTable is { in, out } in USD per 1,000,000 tokens.
 *
 * If a price is unknown/missing, the cost is NOT treated as free — the result
 * is flagged `{ known: false }` and cost is null, so the UI can say "unknown"
 * rather than "$0.00".
 */
export function costLine(tokensIn, tokensOut, priceTable) {
  const ti = Number.isFinite(tokensIn) ? tokensIn : 0;
  const to = Number.isFinite(tokensOut) ? tokensOut : 0;
  const pIn = priceTable && Number.isFinite(priceTable.in) ? priceTable.in : null;
  const pOut = priceTable && Number.isFinite(priceTable.out) ? priceTable.out : null;
  const totalTokens = ti + to;

  if (pIn === null || pOut === null) {
    return {
      known: false,
      tokensIn: ti,
      tokensOut: to,
      totalTokens,
      usd: null,
      text: `${totalTokens.toLocaleString("en-US")} tokens · cost unknown`,
    };
  }

  const usd = (ti / 1_000_000) * pIn + (to / 1_000_000) * pOut;
  return {
    known: true,
    tokensIn: ti,
    tokensOut: to,
    totalTokens,
    usd,
    text: `${totalTokens.toLocaleString("en-US")} tokens · $${usd.toFixed(4)}`,
  };
}

// ---------------------------------------------------------------------------
// Demo fixtures (clearly labeled — the page shows a "Demo data" badge)
// ---------------------------------------------------------------------------

export const DEMO_WORKSPACE = {
  repo: "anthropic/ronin",
  branch: "feat/coding-world-ui",
  model: "claude-opus-4-8",
  priceTable: { in: 15, out: 75 }, // USD per 1M tokens (illustrative)
  tokensIn: 128_400,
  tokensOut: 41_720,
};

export const DEMO_TODOS = [
  { id: "t1", title: "Read existing worlds page for conventions", status: "done" },
  { id: "t2", title: "Design pure logic module + tests", status: "done" },
  { id: "t3", title: "Build three-area workspace layout", status: "in_progress" },
  { id: "t4", title: "Wire approval card to safety floor", status: "pending" },
  { id: "t5", title: "Add diff review + test panels", status: "pending" },
  { id: "t6", title: "Migrate legacy config format", status: "skipped" },
  { id: "t7", title: "Run flaky integration suite", status: "failed" },
];

export const DEMO_FILES = [
  { path: "apps/web/lib/coding_world.mjs", added: 214, removed: 0, status: "added" },
  { path: "apps/web/app/coding/page.tsx", added: 168, removed: 12, status: "modified" },
  { path: "apps/web/app/coding/_components/ApprovalCard.tsx", added: 74, removed: 0, status: "added" },
  { path: "apps/web/lib/legacy_config.json", added: 0, removed: 38, status: "deleted" },
];

export const DEMO_CONVERSATIONS = [
  { id: "c1", title: "Build Coding World UI", turns: 42, active: true },
  { id: "c2", title: "Fix token cost readout", turns: 8, active: false },
  { id: "c3", title: "Investigate flaky test", turns: 15, active: false },
];

export const DEMO_ACTIVITY = [
  { kind: "tool", name: "Read", arg: "apps/web/app/worlds/page.tsx", result: "148 lines" },
  { kind: "tool", name: "Grep", arg: "riskBadge", result: "6 matches in 3 files" },
  { kind: "text", text: "Plan: mirror the worlds page conventions, then add the workspace shell." },
  { kind: "tool", name: "Write", arg: "apps/web/lib/coding_world.mjs", result: "214 lines" },
  { kind: "tool", name: "Bash", arg: "node --test lib/coding_world.test.mjs", result: "awaiting approval" },
];

export const DEMO_TOOLS = [
  { name: "Read", enabled: true },
  { name: "Grep", enabled: true },
  { name: "Glob", enabled: true },
  { name: "Write", enabled: true },
  { name: "Edit", enabled: true },
  { name: "Bash", enabled: false },
];

export const DEMO_SOURCES = [
  { label: "Repo tree", detail: "anthropic/ronin @ feat/coding-world-ui" },
  { label: "CLAUDE.md", detail: "Project conventions" },
  { label: "/api/v1", detail: "Live runtime (at runtime only)" },
];

export const DEMO_MEMORY = [
  { key: "convention", value: "Pure logic in *.mjs, tested with node --test" },
  { key: "safety", value: "Web actions still pass the real safety floor" },
  { key: "style", value: "Calm/premium/technical, light + dark" },
];

export const DEMO_APPROVAL = {
  id: "a1",
  action: { type: "run_command", risk: "destructive" },
  tool: "Bash",
  title: "Run test suite",
  command: "node --test apps/web/lib/coding_world.test.mjs",
  diff: null,
  rationale: "Verify the pure-logic module before proposing the UI wiring.",
};

export const DEMO_WRITE_APPROVAL = {
  id: "a2",
  action: { type: "write_file", risk: "reversible" },
  tool: "Write",
  title: "Create coding_world.mjs",
  command: null,
  diff: { path: "apps/web/lib/coding_world.mjs", added: 214, removed: 0 },
  rationale: "New pure-logic core; no existing file overwritten.",
};

export const DEMO_TESTS = {
  suite: "coding_world.test.mjs",
  total: 14,
  passed: 14,
  failed: 0,
  durationMs: 96,
  cases: [
    { name: "planProgress counts by status", ok: true },
    { name: "riskForTool fail-closed on unknown", ok: true },
    { name: "summarizeDiff totals +/-", ok: true },
    { name: "isApprovalRequired unknown → true", ok: true },
  ],
};

export const DEMO_RUN = {
  workspace: DEMO_WORKSPACE,
  conversations: DEMO_CONVERSATIONS,
  todos: DEMO_TODOS,
  activity: DEMO_ACTIVITY,
  files: DEMO_FILES,
  tools: DEMO_TOOLS,
  sources: DEMO_SOURCES,
  memory: DEMO_MEMORY,
  approvals: [DEMO_APPROVAL, DEMO_WRITE_APPROVAL],
  tests: DEMO_TESTS,
};
