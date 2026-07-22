// Pure, dependency-free logic for the Tasks UI.
// Plain ESM so it runs under `node --test` with zero install, and is imported
// by the React page (app/tasks/page.tsx). No DOM, no fetch, no React here.
//
// This module is the testable core. The page renders from the DEMO_* fixtures
// exported below (clearly labeled demo data); live data comes from /api/v1 at
// runtime. Controls (pause/resume/cancel/retry) are demo-only in the UI and
// never trigger real external actions. High-risk tasks always route through an
// explicit approval step — nothing runs silently.

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

/** Canonical task states. */
export const STATES = [
  "draft",
  "scheduled",
  "queued",
  "running",
  "waiting_for_approval",
  "paused",
  "completed",
  "failed",
  "cancelled",
  "blocked",
];

/** Terminal states — no further transitions allowed. */
export const TERMINAL_STATES = ["completed", "cancelled"];

const BADGES = {
  draft: { label: "Draft", tone: "neutral" },
  scheduled: { label: "Scheduled", tone: "info" },
  queued: { label: "Queued", tone: "info" },
  running: { label: "Running", tone: "info" },
  waiting_for_approval: { label: "Waiting for approval", tone: "warn" },
  paused: { label: "Paused", tone: "warn" },
  completed: { label: "Completed", tone: "ok" },
  failed: { label: "Failed", tone: "danger" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  blocked: { label: "Blocked", tone: "danger" },
};

/** Badge descriptor for a task state; unknown → neutral "Unknown". */
export function stateBadge(state) {
  return BADGES[state] || { label: "Unknown", tone: "neutral" };
}

/** Allowed transitions out of each state. */
const TRANSITIONS = {
  draft: ["scheduled", "queued", "cancelled"],
  scheduled: ["queued", "cancelled", "blocked"],
  queued: ["running", "cancelled", "blocked", "paused"],
  running: ["waiting_for_approval", "paused", "completed", "failed", "cancelled", "blocked"],
  waiting_for_approval: ["running", "cancelled", "failed", "paused"],
  paused: ["running", "queued", "cancelled"],
  failed: ["queued", "cancelled"], // retry re-queues
  blocked: ["queued", "cancelled"],
  completed: [],
  cancelled: [],
};

/**
 * Is a transition from `from` to `to` legal in the state machine?
 * Fail-closed: unknown states or terminal-state departures return false.
 */
export function legalTransition(from, to) {
  if (!Object.prototype.hasOwnProperty.call(TRANSITIONS, from)) return false;
  if (!STATES.includes(to)) return false;
  return TRANSITIONS[from].includes(to);
}

// ---------------------------------------------------------------------------
// Approval — fail closed for high-risk
// ---------------------------------------------------------------------------

/** Risk levels that a task can carry. */
export const HIGH_RISK = "high";

/**
 * Does this task require an explicit human approval step before it may run?
 * Fail-closed: high-risk tasks, any task touching money/external-send/
 * irreversible actions, or tasks with unknown risk ALWAYS require approval.
 * Only tasks explicitly marked low-risk and side-effect-free skip it.
 */
export function needsApproval(task) {
  if (!task || typeof task !== "object") return true;
  if (task.risk === HIGH_RISK) return true;
  if (task.irreversible === true) return true;
  if (Array.isArray(task.effects)) {
    const risky = ["send_email", "payment", "delete", "publish", "external_call"];
    if (task.effects.some((e) => risky.includes(e))) return true;
  }
  // Explicit low-risk + no declared effects → safe to auto-run.
  if (task.risk === "low" && (!task.effects || task.effects.length === 0)) return false;
  // Unknown / unspecified risk → require approval.
  return true;
}

// ---------------------------------------------------------------------------
// Scheduling label
// ---------------------------------------------------------------------------

/**
 * Human label for when a task next runs, relative to `now` (Date | ms | ISO).
 * Handles overdue, soon, and future cases. Non-scheduled tasks → "—".
 */
export function nextRunLabel(task, now = Date.now()) {
  if (!task || !task.next_run_at) return "—";
  const nowMs = now instanceof Date ? now.getTime() : new Date(now).getTime();
  const runMs = new Date(task.next_run_at).getTime();
  if (!Number.isFinite(runMs)) return "—";
  const diff = runMs - nowMs;
  const absMin = Math.round(Math.abs(diff) / 60000);
  if (diff <= 0) {
    if (absMin < 1) return "Due now";
    return `Overdue by ${humanDuration(absMin)}`;
  }
  if (absMin < 1) return "Due now";
  return `Runs in ${humanDuration(absMin)}`;
}

function humanDuration(minutes) {
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"}`;
}

// ---------------------------------------------------------------------------
// Retry
// ---------------------------------------------------------------------------

/**
 * Can this task be retried? Only failed/blocked tasks that have not exhausted
 * their attempt budget. Never mutates. Fail-closed on junk input.
 */
export function retryable(task) {
  if (!task || typeof task !== "object") return false;
  if (task.state !== "failed" && task.state !== "blocked") return false;
  const attempts = Number(task.attempts ?? 0);
  const maxAttempts = Number(task.max_attempts ?? 3);
  return attempts < maxAttempts;
}

// ---------------------------------------------------------------------------
// Demo fixtures (clearly labeled — NOT live data)
// ---------------------------------------------------------------------------

export const DEMO_TASKS = [
  {
    id: "task_brief",
    title: "Weekly founder briefing",
    state: "scheduled",
    risk: "low",
    effects: [],
    attempts: 0,
    max_attempts: 3,
    next_run_at: "2026-07-27T09:00:00Z",
    created_at: "2026-07-01T10:00:00Z",
    timeline: [
      { at: "2026-07-01T10:00:00Z", event: "Created as draft" },
      { at: "2026-07-01T10:01:00Z", event: "Scheduled for Mondays 09:00" },
    ],
  },
  {
    id: "task_email_blast",
    title: "Send re-engagement email to churned users",
    state: "waiting_for_approval",
    risk: "high",
    effects: ["send_email", "external_call"],
    attempts: 0,
    max_attempts: 2,
    next_run_at: "2026-07-22T15:00:00Z",
    created_at: "2026-07-21T09:00:00Z",
    timeline: [
      { at: "2026-07-21T09:00:00Z", event: "Created" },
      { at: "2026-07-22T14:55:00Z", event: "Prepared 42 recipients" },
      { at: "2026-07-22T14:56:00Z", event: "Paused for approval — external send" },
    ],
  },
  {
    id: "task_index",
    title: "Re-index project documents",
    state: "running",
    risk: "low",
    effects: [],
    attempts: 1,
    max_attempts: 3,
    next_run_at: null,
    created_at: "2026-07-22T13:40:00Z",
    timeline: [
      { at: "2026-07-22T13:40:00Z", event: "Queued" },
      { at: "2026-07-22T13:41:00Z", event: "Running — 60% complete" },
    ],
  },
  {
    id: "task_migrate",
    title: "Migrate database schema (v2)",
    state: "failed",
    risk: "high",
    effects: ["delete"],
    irreversible: true,
    attempts: 1,
    max_attempts: 3,
    next_run_at: null,
    created_at: "2026-07-20T08:00:00Z",
    timeline: [
      { at: "2026-07-20T08:00:00Z", event: "Approved by owner" },
      { at: "2026-07-20T08:05:00Z", event: "Failed — connection timeout" },
    ],
  },
  {
    id: "task_report",
    title: "Generate Q2 revenue report",
    state: "completed",
    risk: "low",
    effects: [],
    attempts: 1,
    max_attempts: 3,
    next_run_at: null,
    created_at: "2026-07-15T11:00:00Z",
    timeline: [
      { at: "2026-07-15T11:00:00Z", event: "Queued" },
      { at: "2026-07-15T11:02:00Z", event: "Completed — artifact created" },
    ],
  },
  {
    id: "task_cleanup",
    title: "Archive stale conversations",
    state: "blocked",
    risk: "medium",
    effects: ["delete"],
    attempts: 0,
    max_attempts: 3,
    next_run_at: null,
    created_at: "2026-07-19T16:00:00Z",
    timeline: [
      { at: "2026-07-19T16:00:00Z", event: "Blocked — awaiting retention policy confirmation" },
    ],
  },
];
