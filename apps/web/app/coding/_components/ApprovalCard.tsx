"use client";

import { riskForTool, isApprovalRequired } from "../../../lib/coding_world.mjs";

const TONE_CLASS: Record<string, string> = {
  danger: "bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-300 dark:border-red-900",
  warn: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900",
  ok: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-900",
  info: "bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950/40 dark:text-sky-300 dark:border-sky-900",
  neutral: "bg-neutral-100 text-neutral-600 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:border-neutral-700",
};

export interface Approval {
  id: string;
  action: { type?: string; risk?: string };
  tool: string;
  title: string;
  command: string | null;
  diff: { path: string; added: number; removed: number } | null;
  rationale: string;
}

export function ApprovalCard({
  approval,
  decision,
  onDecide,
}: {
  approval: Approval;
  decision?: "approved" | "rejected";
  onDecide: (id: string, d: "approved" | "rejected") => void;
}) {
  const risk = riskForTool(approval.tool);
  const required = isApprovalRequired(approval.action);

  return (
    <div className="rounded-xl border border-border bg-white p-4 dark:border-neutral-700 dark:bg-neutral-900">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="truncate text-sm font-semibold text-ink dark:text-neutral-100">
            {approval.title}
          </h4>
          <p className="mt-0.5 text-xs text-dim dark:text-neutral-400">
            {approval.action.type ?? "unknown action"} · {approval.tool}
          </p>
        </div>
        <span
          className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium ${TONE_CLASS[risk.tone]}`}
          title={`${risk.label} tool`}
        >
          {risk.label}
        </span>
      </div>

      {approval.command && (
        <pre className="mt-3 overflow-x-auto rounded-md bg-neutral-950 px-3 py-2 font-mono text-xs text-neutral-100">
          <code>$ {approval.command}</code>
        </pre>
      )}

      {approval.diff && (
        <div className="mt-3 flex items-center gap-2 rounded-md border border-border bg-bg px-3 py-2 font-mono text-xs dark:border-neutral-700 dark:bg-neutral-800">
          <span className="truncate text-ink dark:text-neutral-200">{approval.diff.path}</span>
          <span className="ml-auto shrink-0 text-emerald-600 dark:text-emerald-400">+{approval.diff.added}</span>
          <span className="shrink-0 text-red-600 dark:text-red-400">−{approval.diff.removed}</span>
        </div>
      )}

      <p className="mt-3 text-xs text-dim dark:text-neutral-400">{approval.rationale}</p>

      {required && (
        <p className="mt-2 text-[11px] text-dim dark:text-neutral-500">
          Requires approval — web actions still pass the real safety floor.
        </p>
      )}

      {decision ? (
        <p
          className={`mt-3 rounded-md border px-3 py-2 text-center text-xs font-medium ${
            decision === "approved" ? TONE_CLASS.ok : TONE_CLASS.danger
          }`}
          role="status"
        >
          {decision === "approved" ? "Approved (demo — nothing executed)" : "Rejected (demo)"}
        </p>
      ) : (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={() => onDecide(approval.id, "approved")}
            className="flex-1 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent-deep focus:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 dark:focus-visible:ring-offset-neutral-900"
            aria-label={`Approve: ${approval.title}`}
          >
            Approve
          </button>
          <button
            type="button"
            onClick={() => onDecide(approval.id, "rejected")}
            className="flex-1 rounded-md border border-border bg-white px-3 py-1.5 text-xs font-medium text-ink transition hover:bg-bg focus:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200 dark:hover:bg-neutral-700 dark:focus-visible:ring-offset-neutral-900"
            aria-label={`Reject: ${approval.title}`}
          >
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
