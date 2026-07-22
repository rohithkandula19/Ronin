"use client";

import { useMemo, useState } from "react";
import {
  DEMO_TASKS,
  legalTransition,
  needsApproval,
  nextRunLabel,
  retryable,
  stateBadge,
} from "../../lib/tasks_ui.mjs";

const TONE: Record<string, string> = {
  danger: "bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-300 dark:border-red-900",
  warn: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900",
  ok: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-900",
  info: "bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950/40 dark:text-sky-300 dark:border-sky-900",
  neutral: "bg-neutral-100 text-neutral-600 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:border-neutral-700",
};

type Task = (typeof DEMO_TASKS)[number] & { state: string };

// Fixed reference "now" (ms) so the demo renders deterministically.
const NOW = new Date("2026-07-22T12:00:00Z").getTime();

function fmtTime(iso: string) {
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 16) + "Z";
  } catch {
    return iso;
  }
}

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>(DEMO_TASKS as Task[]);
  const [open, setOpen] = useState<string | null>(DEMO_TASKS[1]?.id ?? null);

  // Demo-only control: attempt a state change, guarded by the state machine.
  function control(id: string, to: string, note: string) {
    setTasks((prev) =>
      prev.map((t) => {
        if (t.id !== id) return t;
        if (!legalTransition(t.state, to)) return t;
        return {
          ...t,
          state: to,
          timeline: [...(t.timeline || []), { at: new Date().toISOString(), event: note }],
        };
      })
    );
  }

  return (
    <main className="mx-auto max-w-4xl px-6 py-10 text-ink dark:text-neutral-100">
      <header className="mb-8">
        <p className="text-sm font-medium text-dim">Ronin AI OS</p>
        <h1 className="text-3xl font-bold tracking-tight">Tasks</h1>
        <p className="mt-2 max-w-2xl text-dim">
          Background work Ronin runs on your behalf. Every task has an explicit state, a
          timeline, and controls. High-risk or irreversible tasks never run silently — they
          pause for your approval first.
        </p>
        <p className="mt-3 inline-block rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
          Demo mode — tasks are labeled fixtures and controls are inert. Live tasks load from <code>/api/v1</code> at runtime.
        </p>
      </header>

      <ul className="space-y-3">
        {tasks.map((task) => {
          const badge = stateBadge(task.state);
          const approval = needsApproval(task);
          const canRetry = retryable(task);
          const isOpen = open === task.id;
          return (
            <li key={task.id} className="rounded-xl border border-border bg-white dark:border-neutral-800 dark:bg-neutral-900">
              <div className="flex flex-wrap items-center gap-3 p-4">
                <button
                  onClick={() => setOpen(isOpen ? null : task.id)}
                  aria-expanded={isOpen}
                  className="flex-1 text-left focus:outline-none focus:ring-2 focus:ring-accent rounded"
                >
                  <span className="font-medium">{task.title}</span>
                </button>
                <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${TONE[badge.tone]}`}>
                  {badge.label}
                </span>
                <span className="text-xs text-dim">{nextRunLabel(task, NOW)}</span>
              </div>

              {approval && task.state === "waiting_for_approval" && (
                <div className="mx-4 mb-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
                  <p className="font-medium">Approval required — this task performs high-risk actions
                    {task.effects && task.effects.length > 0 ? ` (${task.effects.join(", ")})` : ""}.</p>
                  <p className="mt-1 text-xs">It will not proceed until you approve. Nothing external has happened yet.</p>
                  <div className="mt-2 flex gap-2">
                    <button onClick={() => control(task.id, "running", "Approved by owner")} className="btn btn-primary text-xs">Approve &amp; run</button>
                    <button onClick={() => control(task.id, "cancelled", "Rejected by owner")} className="btn btn-secondary text-xs">Reject</button>
                  </div>
                </div>
              )}

              {isOpen && (
                <div className="border-t border-border px-4 py-4 dark:border-neutral-800">
                  <div className="mb-3 flex flex-wrap gap-2 text-xs">
                    <span className="text-dim">Risk: <b className="text-ink dark:text-neutral-100">{task.risk}</b></span>
                    <span className="text-dim">Attempts: <b className="text-ink dark:text-neutral-100">{task.attempts}/{task.max_attempts}</b></span>
                    {approval && <span className="rounded bg-amber-100 px-1.5 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300">Approval-gated</span>}
                  </div>

                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-dim">Timeline</h3>
                  <ol className="mb-4 space-y-2 border-l border-border pl-4 dark:border-neutral-700">
                    {(task.timeline || []).map((e: { at: string; event: string }, i: number) => (
                      <li key={i} className="relative text-sm">
                        <span className="absolute -left-[1.30rem] top-1.5 h-2 w-2 rounded-full bg-accent" aria-hidden />
                        <span className="text-ink dark:text-neutral-100">{e.event}</span>
                        <span className="ml-2 text-xs text-dim">{fmtTime(e.at)}</span>
                      </li>
                    ))}
                  </ol>

                  <div className="flex flex-wrap gap-2">
                    {legalTransition(task.state, "paused") && (
                      <button onClick={() => control(task.id, "paused", "Paused by owner")} className="btn btn-secondary text-xs">Pause</button>
                    )}
                    {legalTransition(task.state, "running") && task.state === "paused" && (
                      <button onClick={() => control(task.id, "running", "Resumed by owner")} className="btn btn-secondary text-xs">Resume</button>
                    )}
                    {legalTransition(task.state, "cancelled") && (
                      <button onClick={() => control(task.id, "cancelled", "Cancelled by owner")} className="btn btn-secondary text-xs">Cancel</button>
                    )}
                    {canRetry && (
                      <button onClick={() => control(task.id, "queued", "Re-queued for retry")} className="btn btn-secondary text-xs">Retry</button>
                    )}
                  </div>
                  <p className="mt-2 text-xs text-dim">Controls are demo-only and do not trigger real actions.</p>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </main>
  );
}
