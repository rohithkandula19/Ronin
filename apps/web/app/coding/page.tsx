"use client";

import { useMemo, useState } from "react";
import {
  DEMO_RUN,
  planProgress,
  planGlyph,
  PLAN_STATUS,
  riskForTool,
  summarizeDiff,
  formatToolCall,
  formatToolResult,
  costLine,
} from "../../lib/coding_world.mjs";
import { ApprovalCard, type Approval } from "./_components/ApprovalCard";

const TONE_CLASS: Record<string, string> = {
  danger: "text-red-600 dark:text-red-400",
  warn: "text-amber-600 dark:text-amber-400",
  ok: "text-emerald-600 dark:text-emerald-400",
  info: "text-sky-600 dark:text-sky-400",
  neutral: "text-dim dark:text-neutral-400",
};

const BADGE_CLASS: Record<string, string> = {
  danger: "bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-300 dark:border-red-900",
  warn: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900",
  ok: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-900",
  info: "bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950/40 dark:text-sky-300 dark:border-sky-900",
  neutral: "bg-neutral-100 text-neutral-600 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:border-neutral-700",
};

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-border bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-dim dark:text-neutral-500">
        {title}
      </h3>
      {children}
    </section>
  );
}

export default function CodingWorld() {
  const run = DEMO_RUN;
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [decisions, setDecisions] = useState<Record<string, "approved" | "rejected">>({});

  const progress = useMemo(() => planProgress(run.todos), [run.todos]);
  const diff = useMemo(() => summarizeDiff(run.files), [run.files]);
  const cost = useMemo(
    () =>
      costLine(run.workspace.tokensIn, run.workspace.tokensOut, run.workspace.priceTable),
    [run.workspace]
  );

  const decide = (id: string, d: "approved" | "rejected") =>
    setDecisions((prev) => ({ ...prev, [id]: d }));

  return (
    <main className="min-h-screen bg-bg text-ink dark:bg-neutral-950 dark:text-neutral-100">
      {/* Header */}
      <header className="border-b border-border bg-white px-4 py-3 dark:border-neutral-800 dark:bg-neutral-900 sm:px-6">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-dim dark:text-neutral-500">Ronin AI OS</span>
            <span className="text-dim dark:text-neutral-600">/</span>
            <h1 className="text-sm font-semibold">Coding World</h1>
            <span className="rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
              Demo data
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-xs text-dim dark:text-neutral-400 sm:ml-auto">
            <span title="Repository">
              <span className="text-neutral-400 dark:text-neutral-600">repo </span>
              <span className="text-ink dark:text-neutral-200">{run.workspace.repo}</span>
            </span>
            <span title="Branch">
              <span className="text-neutral-400 dark:text-neutral-600">branch </span>
              <span className="text-ink dark:text-neutral-200">{run.workspace.branch}</span>
            </span>
            <span title="Model">
              <span className="text-neutral-400 dark:text-neutral-600">model </span>
              <span className="text-ink dark:text-neutral-200">{run.workspace.model}</span>
            </span>
            <span title="Token usage and estimated cost" className={cost.known ? "" : "text-amber-600 dark:text-amber-400"}>
              {cost.text}
            </span>
          </div>
        </div>
        <p className="mt-2 text-[11px] text-dim dark:text-neutral-500">
          This is the workspace shell. The agent runs server-side; live data comes from{" "}
          <code className="rounded bg-bg px-1 dark:bg-neutral-800">/api/v1</code> at runtime.
          Nothing here executes — approvals and runs are illustrative.
        </p>

        <div className="mt-2 flex gap-2 lg:hidden">
          <button
            type="button"
            onClick={() => setLeftOpen((v) => !v)}
            className="rounded-md border border-border px-2 py-1 text-xs dark:border-neutral-700"
            aria-expanded={leftOpen}
          >
            {leftOpen ? "Hide" : "Show"} files
          </button>
          <button
            type="button"
            onClick={() => setRightOpen((v) => !v)}
            className="rounded-md border border-border px-2 py-1 text-xs dark:border-neutral-700"
            aria-expanded={rightOpen}
          >
            {rightOpen ? "Hide" : "Show"} panels
          </button>
        </div>
      </header>

      {/* Three-area layout */}
      <div className="flex flex-col gap-4 p-4 lg:flex-row lg:items-start sm:p-6">
        {/* LEFT: file tree + conversations */}
        {leftOpen && (
          <aside className="w-full shrink-0 space-y-4 lg:w-64">
            <div className="hidden justify-end lg:flex">
              <button
                type="button"
                onClick={() => setLeftOpen(false)}
                className="text-xs text-dim hover:text-ink dark:text-neutral-500 dark:hover:text-neutral-200"
                aria-label="Collapse left sidebar"
              >
                ‹ collapse
              </button>
            </div>
            <Panel title="Files">
              <ul className="space-y-1 font-mono text-xs">
                {diff.perFile.map((f) => (
                  <li key={f.path} className="flex items-center gap-2">
                    <span
                      className={
                        f.status === "added"
                          ? TONE_CLASS.ok
                          : f.status === "deleted"
                          ? TONE_CLASS.danger
                          : TONE_CLASS.neutral
                      }
                      aria-hidden
                    >
                      {f.status === "added" ? "A" : f.status === "deleted" ? "D" : "M"}
                    </span>
                    <span className="truncate text-ink dark:text-neutral-300" title={f.path}>
                      {f.path.split("/").pop()}
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>
            <Panel title="Conversations">
              <ul className="space-y-1">
                {run.conversations.map((c) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-xs transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                        c.active
                          ? "bg-bg font-medium text-ink dark:bg-neutral-800 dark:text-neutral-100"
                          : "text-dim hover:bg-bg dark:text-neutral-400 dark:hover:bg-neutral-800"
                      }`}
                    >
                      <span className="truncate">{c.title}</span>
                      <span className="shrink-0 text-neutral-400 dark:text-neutral-600">{c.turns}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </Panel>
          </aside>
        )}

        {/* CENTER: agent activity */}
        <div className="min-w-0 flex-1 space-y-4">
          <Panel title="Plan">
            <div className="mb-3 flex items-center gap-3">
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-bg dark:bg-neutral-800">
                <div
                  className="h-full rounded-full bg-accent transition-all"
                  style={{ width: `${progress.percent}%` }}
                  role="progressbar"
                  aria-valuenow={progress.percent}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label="Plan completion"
                />
              </div>
              <span className="shrink-0 font-mono text-xs text-dim dark:text-neutral-400">
                {progress.counts.done}/{progress.total} · {progress.percent}%
              </span>
            </div>
            <ul className="space-y-1.5">
              {run.todos.map((t) => {
                const meta = PLAN_STATUS[t.status as keyof typeof PLAN_STATUS] ?? PLAN_STATUS.pending;
                return (
                  <li key={t.id} className="flex items-start gap-2 text-sm">
                    <span className={`mt-0.5 font-mono ${TONE_CLASS[meta.tone]}`} aria-hidden>
                      {planGlyph(t.status)}
                    </span>
                    <span
                      className={
                        t.status === "done"
                          ? "text-dim line-through dark:text-neutral-500"
                          : t.status === "skipped" || t.status === "failed"
                          ? "text-dim dark:text-neutral-500"
                          : "text-ink dark:text-neutral-200"
                      }
                    >
                      {t.title}
                      <span className="sr-only"> ({meta.label})</span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </Panel>

          <Panel title="Agent activity">
            <div className="space-y-2 font-mono text-xs">
              {run.activity.map((a, i) =>
                a.kind === "tool" ? (
                  <div key={i}>
                    <div className="flex items-center gap-2">
                      <span className="text-ink dark:text-neutral-200">
                        {formatToolCall(a.name, a.arg)}
                      </span>
                      <span
                        className={`rounded-full border px-1.5 text-[10px] ${
                          BADGE_CLASS[riskForTool(a.name!).tone]
                        }`}
                      >
                        {riskForTool(a.name!).label}
                      </span>
                    </div>
                    {a.result && (
                      <div className="ml-2 text-dim dark:text-neutral-500">
                        {formatToolResult(a.result)}
                      </div>
                    )}
                  </div>
                ) : (
                  <p key={i} className="text-neutral-700 dark:text-neutral-300">
                    {a.text}
                  </p>
                )
              )}
              <p className="text-neutral-400 dark:text-neutral-600" aria-live="polite">
                <span className="inline-block animate-pulse">▍</span> streaming next turn…
              </p>
            </div>
          </Panel>

          {/* Diff review */}
          <Panel title="Diff review">
            <div className="mb-3 flex items-center gap-4 text-xs">
              <span className="text-dim dark:text-neutral-400">{diff.files} files</span>
              <span className="font-mono text-emerald-600 dark:text-emerald-400">+{diff.added}</span>
              <span className="font-mono text-red-600 dark:text-red-400">−{diff.removed}</span>
              <span className="font-mono text-dim dark:text-neutral-500">net {diff.net >= 0 ? "+" : ""}{diff.net}</span>
            </div>
            <ul className="space-y-1">
              {diff.perFile.map((f) => (
                <li
                  key={f.path}
                  className="flex items-center gap-3 rounded-md border border-border bg-bg px-3 py-1.5 font-mono text-xs dark:border-neutral-800 dark:bg-neutral-800/50"
                >
                  <span className="truncate text-ink dark:text-neutral-300" title={f.path}>
                    {f.path}
                  </span>
                  <span className="ml-auto shrink-0 text-emerald-600 dark:text-emerald-400">+{f.added}</span>
                  <span className="shrink-0 text-red-600 dark:text-red-400">−{f.removed}</span>
                </li>
              ))}
            </ul>
          </Panel>

          {/* Test / verification results */}
          <Panel title="Verification">
            <div className="flex items-center gap-3">
              <span
                className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                  run.tests.failed === 0 ? BADGE_CLASS.ok : BADGE_CLASS.danger
                }`}
              >
                {run.tests.failed === 0 ? "Green" : "Failing"}
              </span>
              <span className="font-mono text-xs text-dim dark:text-neutral-400">
                {run.tests.passed}/{run.tests.total} passed · {run.tests.durationMs}ms · {run.tests.suite}
              </span>
            </div>
            <ul className="mt-3 space-y-1 font-mono text-xs">
              {run.tests.cases.map((c) => (
                <li key={c.name} className="flex items-center gap-2">
                  <span className={c.ok ? TONE_CLASS.ok : TONE_CLASS.danger} aria-hidden>
                    {c.ok ? "✓" : "✗"}
                  </span>
                  <span className="text-ink dark:text-neutral-300">{c.name}</span>
                </li>
              ))}
            </ul>
          </Panel>
        </div>

        {/* RIGHT: panels */}
        {rightOpen && (
          <aside className="w-full shrink-0 space-y-4 lg:w-72">
            <div className="hidden justify-end lg:flex">
              <button
                type="button"
                onClick={() => setRightOpen(false)}
                className="text-xs text-dim hover:text-ink dark:text-neutral-500 dark:hover:text-neutral-200"
                aria-label="Collapse right sidebar"
              >
                collapse ›
              </button>
            </div>

            <Panel title="Model">
              <p className="font-mono text-xs text-ink dark:text-neutral-200">{run.workspace.model}</p>
              <p className="mt-1 text-xs text-dim dark:text-neutral-400">{cost.text}</p>
            </Panel>

            <Panel title="Tools">
              <ul className="space-y-1.5">
                {run.tools.map((t) => {
                  const risk = riskForTool(t.name);
                  return (
                    <li key={t.name} className="flex items-center gap-2 text-xs">
                      <span
                        className={`h-1.5 w-1.5 rounded-full ${t.enabled ? "bg-emerald-500" : "bg-neutral-300 dark:bg-neutral-600"}`}
                        aria-hidden
                      />
                      <span className="font-mono text-ink dark:text-neutral-200">{t.name}</span>
                      <span className={`ml-auto ${TONE_CLASS[risk.tone]}`}>{risk.label}</span>
                    </li>
                  );
                })}
              </ul>
            </Panel>

            <Panel title="Sources">
              <ul className="space-y-2">
                {run.sources.map((s) => (
                  <li key={s.label} className="text-xs">
                    <p className="font-medium text-ink dark:text-neutral-200">{s.label}</p>
                    <p className="text-dim dark:text-neutral-400">{s.detail}</p>
                  </li>
                ))}
              </ul>
            </Panel>

            <Panel title="Actions / Approvals">
              <div className="space-y-3">
                {run.approvals.map((a) => (
                  <ApprovalCard
                    key={a.id}
                    approval={a as Approval}
                    decision={decisions[a.id]}
                    onDecide={decide}
                  />
                ))}
              </div>
            </Panel>

            <Panel title="Memory">
              <ul className="space-y-2">
                {run.memory.map((m) => (
                  <li key={m.key} className="text-xs">
                    <span className="font-mono text-accent">{m.key}</span>
                    <p className="text-dim dark:text-neutral-400">{m.value}</p>
                  </li>
                ))}
              </ul>
            </Panel>
          </aside>
        )}
      </div>
    </main>
  );
}
