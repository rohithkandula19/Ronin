// Tasks UI logic tests — run with `node --test` (no install required).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEMO_TASKS,
  STATES,
  TERMINAL_STATES,
  legalTransition,
  needsApproval,
  nextRunLabel,
  retryable,
  stateBadge,
} from "./tasks_ui.mjs";

test("stateBadge maps every canonical state to a label + tone", () => {
  for (const s of STATES) {
    const b = stateBadge(s);
    assert.ok(b.label && b.tone, `${s} has badge`);
  }
  assert.equal(stateBadge("running").tone, "info");
  assert.equal(stateBadge("failed").tone, "danger");
  assert.equal(stateBadge("bogus").label, "Unknown");
});

test("legalTransition allows valid moves", () => {
  assert.equal(legalTransition("queued", "running"), true);
  assert.equal(legalTransition("running", "waiting_for_approval"), true);
  assert.equal(legalTransition("waiting_for_approval", "running"), true);
  assert.equal(legalTransition("paused", "running"), true);
  assert.equal(legalTransition("failed", "queued"), true);
});

test("legalTransition rejects illegal and terminal-state moves", () => {
  for (const t of TERMINAL_STATES) {
    assert.equal(legalTransition(t, "running"), false, `${t} is terminal`);
  }
  assert.equal(legalTransition("draft", "completed"), false);
  assert.equal(legalTransition("bogus", "running"), false);
  assert.equal(legalTransition("running", "bogus"), false);
});

test("needsApproval fails closed for high-risk tasks", () => {
  assert.equal(needsApproval({ risk: "high" }), true);
});

test("needsApproval flags irreversible and risky effects", () => {
  assert.equal(needsApproval({ risk: "low", irreversible: true }), true);
  assert.equal(needsApproval({ risk: "low", effects: ["send_email"] }), true);
  assert.equal(needsApproval({ risk: "low", effects: ["payment"] }), true);
});

test("needsApproval only skips explicit low-risk with no effects", () => {
  assert.equal(needsApproval({ risk: "low", effects: [] }), false);
  assert.equal(needsApproval({ risk: "low" }), false);
});

test("needsApproval requires approval on unknown/missing risk and junk input", () => {
  assert.equal(needsApproval({}), true);
  assert.equal(needsApproval({ risk: "medium" }), true);
  assert.equal(needsApproval(null), true);
});

test("nextRunLabel handles future, overdue, due-now, and non-scheduled", () => {
  const now = new Date("2026-07-22T12:00:00Z");
  assert.match(nextRunLabel({ next_run_at: "2026-07-22T13:00:00Z" }, now), /Runs in 1 hr/);
  assert.match(nextRunLabel({ next_run_at: "2026-07-22T10:00:00Z" }, now), /Overdue by 2 hr/);
  assert.equal(nextRunLabel({ next_run_at: "2026-07-22T12:00:00Z" }, now), "Due now");
  assert.equal(nextRunLabel({ next_run_at: null }, now), "—");
  assert.equal(nextRunLabel(null, now), "—");
});

test("nextRunLabel renders day granularity for far-future runs", () => {
  const now = new Date("2026-07-22T12:00:00Z");
  assert.match(nextRunLabel({ next_run_at: "2026-07-25T12:00:00Z" }, now), /3 days/);
});

test("retryable only for failed/blocked within attempt budget", () => {
  assert.equal(retryable({ state: "failed", attempts: 1, max_attempts: 3 }), true);
  assert.equal(retryable({ state: "blocked", attempts: 0, max_attempts: 3 }), true);
  assert.equal(retryable({ state: "failed", attempts: 3, max_attempts: 3 }), false);
  assert.equal(retryable({ state: "running" }), false);
  assert.equal(retryable({ state: "completed" }), false);
  assert.equal(retryable(null), false);
});

test("demo fixtures are internally consistent with the logic", () => {
  const emailBlast = DEMO_TASKS.find((t) => t.id === "task_email_blast");
  assert.equal(needsApproval(emailBlast), true);
  assert.equal(emailBlast.state, "waiting_for_approval");
  const brief = DEMO_TASKS.find((t) => t.id === "task_brief");
  assert.equal(needsApproval(brief), false);
  const migrate = DEMO_TASKS.find((t) => t.id === "task_migrate");
  assert.equal(retryable(migrate), true);
});
