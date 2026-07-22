// Coding World logic tests — run with `node --test` (no install required).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  planProgress,
  planGlyph,
  riskForTool,
  summarizeDiff,
  formatToolCall,
  formatToolResult,
  isApprovalRequired,
  costLine,
  DEMO_RUN,
  DEMO_TODOS,
  DEMO_FILES,
} from "./coding_world.mjs";

test("planProgress counts by status and computes percent", () => {
  const todos = [
    { status: "done" },
    { status: "done" },
    { status: "in_progress" },
    { status: "pending" },
  ];
  const p = planProgress(todos);
  assert.equal(p.total, 4);
  assert.equal(p.counts.done, 2);
  assert.equal(p.counts.in_progress, 1);
  assert.equal(p.counts.pending, 1);
  assert.equal(p.percent, 50);
});

test("planProgress handles empty / non-array input as 0%", () => {
  assert.equal(planProgress([]).percent, 0);
  assert.equal(planProgress(undefined).total, 0);
  assert.equal(planProgress(null).counts.failed, 0);
});

test("planProgress ignores unknown statuses in counts", () => {
  const p = planProgress([{ status: "bogus" }, { status: "done" }]);
  assert.equal(p.total, 2);
  assert.equal(p.counts.done, 1);
  assert.equal(p.percent, 50);
});

test("planGlyph maps statuses to glyphs and falls back to pending box", () => {
  assert.equal(planGlyph("done"), "✓");
  assert.equal(planGlyph("in_progress"), "▶");
  assert.equal(planGlyph("skipped"), "⊘");
  assert.equal(planGlyph("failed"), "✗");
  assert.equal(planGlyph("pending"), "☐");
  assert.equal(planGlyph("whatever"), "☐");
});

test("riskForTool maps read-only / reversible / destructive", () => {
  assert.equal(riskForTool("Read").tier, "read_only");
  assert.equal(riskForTool("Grep").tone, "ok");
  assert.equal(riskForTool("Write").tier, "reversible");
  assert.equal(riskForTool("Bash").tier, "destructive");
  assert.equal(riskForTool("Bash").tone, "danger");
});

test("riskForTool is fail-closed: unknown tool is 'unknown', never read-only", () => {
  const r = riskForTool("SomeNewTool");
  assert.equal(r.tier, "unknown");
  assert.notEqual(r.tier, "read_only");
  assert.equal(riskForTool("").tier, "unknown");
  assert.equal(riskForTool(null).tier, "unknown");
});

test("summarizeDiff totals +/- and gives per-file net", () => {
  const s = summarizeDiff([
    { path: "a.ts", added: 10, removed: 2 },
    { path: "b.ts", added: 5, removed: 5 },
  ]);
  assert.equal(s.files, 2);
  assert.equal(s.added, 15);
  assert.equal(s.removed, 7);
  assert.equal(s.net, 8);
  assert.equal(s.perFile[0].net, 8);
  assert.equal(s.perFile[1].net, 0);
});

test("summarizeDiff tolerates missing counts and bad input", () => {
  const s = summarizeDiff([{ path: "x.ts" }, {}]);
  assert.equal(s.added, 0);
  assert.equal(s.removed, 0);
  assert.equal(s.perFile[1].path, "(unknown)");
  assert.equal(s.perFile[1].status, "modified");
  assert.equal(summarizeDiff(null).files, 0);
});

test("formatToolCall renders ⏺ name(arg)", () => {
  assert.equal(formatToolCall("Read", "file.ts"), "⏺ Read(file.ts)");
  assert.equal(formatToolCall("Grep", "pattern"), "⏺ Grep(pattern)");
});

test("formatToolCall handles empty/nullish args and non-strings", () => {
  assert.equal(formatToolCall("Bash", ""), "⏺ Bash()");
  assert.equal(formatToolCall("Bash", null), "⏺ Bash()");
  assert.equal(formatToolCall("Bash"), "⏺ Bash()");
  assert.equal(formatToolCall("", "x"), "⏺ tool(x)");
  assert.equal(formatToolCall("Count", 3), "⏺ Count(3)");
});

test("formatToolResult renders ⎿ line", () => {
  assert.equal(formatToolResult("148 lines"), "⎿ 148 lines");
  assert.equal(formatToolResult(null), "⎿ ");
});

test("isApprovalRequired is fail-closed for unknown/malformed actions", () => {
  assert.equal(isApprovalRequired("read_file"), false);
  assert.equal(isApprovalRequired("search"), false);
  assert.equal(isApprovalRequired("run_command"), true);
  assert.equal(isApprovalRequired("totally_unknown"), true);
  assert.equal(isApprovalRequired(undefined), true);
  assert.equal(isApprovalRequired(null), true);
  assert.equal(isApprovalRequired(42), true);
  assert.equal(isApprovalRequired({}), true); // missing type → fail closed
});

test("isApprovalRequired: destructive risk always requires approval", () => {
  assert.equal(isApprovalRequired({ type: "read_file", risk: "destructive" }), true);
  assert.equal(isApprovalRequired({ type: "read_file", risk: "read_only" }), false);
  assert.equal(isApprovalRequired({ type: "write_file" }), true);
});

test("costLine computes USD from a price table", () => {
  const c = costLine(1_000_000, 1_000_000, { in: 15, out: 75 });
  assert.equal(c.known, true);
  assert.equal(c.totalTokens, 2_000_000);
  assert.ok(Math.abs(c.usd - 90) < 1e-9);
  assert.match(c.text, /\$90\.0000/);
});

test("costLine: unknown price is NOT treated as free", () => {
  const c = costLine(1000, 500, null);
  assert.equal(c.known, false);
  assert.equal(c.usd, null);
  assert.match(c.text, /cost unknown/);
  const c2 = costLine(1000, 500, { in: 15 }); // missing out
  assert.equal(c2.known, false);
  assert.equal(c2.usd, null);
});

test("demo fixtures are exported and internally consistent", () => {
  assert.ok(Array.isArray(DEMO_TODOS) && DEMO_TODOS.length >= 5);
  assert.ok(Array.isArray(DEMO_FILES) && DEMO_FILES.length >= 1);
  assert.equal(DEMO_RUN.todos, DEMO_TODOS);
  assert.equal(DEMO_RUN.files, DEMO_FILES);
  // The demo tests fixture should honestly report all-passing counts.
  assert.equal(DEMO_RUN.tests.passed + DEMO_RUN.tests.failed, DEMO_RUN.tests.total);
  // Every approval in the demo run should actually require approval (fail-closed floor).
  for (const a of DEMO_RUN.approvals) {
    assert.equal(isApprovalRequired(a.action), true);
  }
});
