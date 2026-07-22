"use client";

import { useMemo, useState } from "react";
import {
  DEMO_MEMORY_ITEMS,
  DEMO_WORLDS,
  SCOPES,
  canAutoSurface,
  groupByScope,
  isTrainingEligible,
  retentionLabel,
  scopeLabel,
  transferPreview,
} from "../../lib/vault_ui.mjs";

const TONE: Record<string, string> = {
  danger: "bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-300 dark:border-red-900",
  warn: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900",
  ok: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-900",
  neutral: "bg-neutral-100 text-neutral-600 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:border-neutral-700",
};

type MemoryItem = (typeof DEMO_MEMORY_ITEMS)[number];

function Pill({ tone, children }: { tone: string; children: React.ReactNode }) {
  return (
    <span className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-xs ${TONE[tone] ?? TONE.neutral}`}>
      {children}
    </span>
  );
}

function fmtDate(iso: string) {
  try {
    return new Date(iso).toISOString().slice(0, 10);
  } catch {
    return iso;
  }
}

export default function VaultPage() {
  const [items] = useState<MemoryItem[]>(DEMO_MEMORY_ITEMS);
  const [deleted, setDeleted] = useState<Set<string>>(new Set());
  const [from, setFrom] = useState<"software" | "healthcare">("healthcare");
  const [to, setTo] = useState<"software" | "healthcare">("software");
  const [showPreview, setShowPreview] = useState(false);
  const [confirmed, setConfirmed] = useState(false);

  const live = useMemo(() => items.filter((i) => !deleted.has(i.id)), [items, deleted]);
  const groups = useMemo(
    () => groupByScope(live) as unknown as Record<string, MemoryItem[]>,
    [live]
  );

  const preview = useMemo(
    () => transferPreview(live, DEMO_WORLDS[from], DEMO_WORLDS[to]),
    [live, from, to]
  );

  return (
    <main className="mx-auto max-w-5xl px-6 py-10 text-ink dark:text-neutral-100">
      <header className="mb-8">
        <p className="text-sm font-medium text-dim">Ronin AI OS</p>
        <h1 className="text-3xl font-bold tracking-tight">Vault — your memory</h1>
        <p className="mt-2 max-w-2xl text-dim">
          Everything Ronin remembers, grouped by scope. You own it: inspect the source and
          confidence of each item, see how long it is kept, whether it may be used for training,
          who can see it, and delete anything at will.
        </p>
        <p className="mt-3 inline-block rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
          Demo mode — items below are labeled fixtures. Live memory loads from <code>/api/v1</code> at runtime.
        </p>
      </header>

      {SCOPES.map((scope) => {
        const bucket: MemoryItem[] = groups[scope] ?? [];
        if (bucket.length === 0) return null;
        return (
          <section key={scope} className="mb-8">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-dim">
              {scopeLabel(scope)}{" "}
              <span className="font-normal text-neutral-400">· {bucket.length}</span>
            </h2>
            <ul className="grid gap-3 sm:grid-cols-2">
              {bucket.map((item) => {
                const trainable = isTrainingEligible(item);
                const crossesToSoftware = canAutoSurface(item, DEMO_WORLDS.software);
                const crossesToHealth = canAutoSurface(item, DEMO_WORLDS.healthcare);
                return (
                  <li
                    key={item.id}
                    className="rounded-xl border border-border bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="font-medium leading-snug">{item.summary}</p>
                      <Pill tone={item.sensitivity === "normal" ? "neutral" : "danger"}>
                        {item.sensitivity}
                      </Pill>
                    </div>
                    <dl className="mt-3 space-y-1 text-xs text-dim">
                      <div><span className="font-medium text-neutral-700 dark:text-neutral-300">Owner:</span> {item.owner}</div>
                      <div><span className="font-medium text-neutral-700 dark:text-neutral-300">Source:</span> {item.source}</div>
                      <div>
                        <span className="font-medium text-neutral-700 dark:text-neutral-300">Created:</span> {fmtDate(item.created_at)}
                        {" · "}
                        <span className="font-medium text-neutral-700 dark:text-neutral-300">Updated:</span> {fmtDate(item.updated_at)}
                      </div>
                      <div><span className="font-medium text-neutral-700 dark:text-neutral-300">Confidence:</span> {Math.round(item.confidence * 100)}%</div>
                      <div><span className="font-medium text-neutral-700 dark:text-neutral-300">Retention:</span> {retentionLabel(item.retention)}</div>
                      <div><span className="font-medium text-neutral-700 dark:text-neutral-300">Visibility:</span> {item.visibility}</div>
                    </dl>
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      <Pill tone={trainable ? "ok" : "neutral"}>
                        {trainable ? "Training-eligible" : "Not for training"}
                      </Pill>
                      <Pill tone={crossesToSoftware ? "warn" : "neutral"}>
                        {crossesToSoftware ? "Surfaces in Coding" : "Isolated from Coding"}
                      </Pill>
                      <Pill tone={crossesToHealth ? "warn" : "neutral"}>
                        {crossesToHealth ? "Surfaces in Healthcare" : "Isolated from Healthcare"}
                      </Pill>
                    </div>
                    <div className="mt-3">
                      <button
                        onClick={() => setDeleted((d) => new Set(d).add(item.id))}
                        className="rounded-md border border-red-200 px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-red-400 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950/40"
                      >
                        Delete
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}

      {/* Cross-industry transfer preview — requires an explicit action. */}
      <section className="mt-12 rounded-xl border border-border bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
        <h2 className="text-lg font-semibold">Cross-industry transfer preview</h2>
        <p className="mt-1 text-sm text-dim">
          Memory is isolated per industry. Before any context moves between worlds, review
          exactly what would transfer and what is blocked. Nothing moves automatically — you
          must explicitly confirm.
        </p>

        <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
          <label className="flex items-center gap-2">
            From
            <select
              value={from}
              onChange={(e) => { setFrom(e.target.value as "software" | "healthcare"); setConfirmed(false); }}
              className="rounded-md border border-border bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-800"
            >
              <option value="healthcare">Healthcare</option>
              <option value="software">Coding</option>
            </select>
          </label>
          <span aria-hidden>→</span>
          <label className="flex items-center gap-2">
            To
            <select
              value={to}
              onChange={(e) => { setTo(e.target.value as "software" | "healthcare"); setConfirmed(false); }}
              className="rounded-md border border-border bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-800"
            >
              <option value="software">Coding</option>
              <option value="healthcare">Healthcare</option>
            </select>
          </label>
          <button
            onClick={() => { setShowPreview(true); setConfirmed(false); }}
            className="btn btn-secondary text-sm"
          >
            Preview transfer
          </button>
        </div>

        {showPreview && (
          <div className="mt-5 space-y-4">
            <p className="text-sm text-dim">{preview.summary}{preview.crossIndustry ? " (cross-industry)" : ""}</p>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <h3 className="mb-2 text-sm font-semibold text-emerald-700 dark:text-emerald-400">Would transfer ({preview.moves.length})</h3>
                <ul className="space-y-1 text-sm">
                  {preview.moves.map((m: MemoryItem) => <li key={m.id} className="rounded-md border border-border px-2 py-1 dark:border-neutral-800">{m.summary}</li>)}
                  {preview.moves.length === 0 && <li className="text-dim">Nothing.</li>}
                </ul>
              </div>
              <div>
                <h3 className="mb-2 text-sm font-semibold text-red-700 dark:text-red-400">Blocked by isolation ({preview.blocked.length})</h3>
                <ul className="space-y-1 text-sm">
                  {preview.blocked.map((b: { item: MemoryItem; reasons: string[] }) => (
                    <li key={b.item.id} className="rounded-md border border-red-200 bg-red-50 px-2 py-1 dark:border-red-900 dark:bg-red-950/40">
                      {b.item.summary}
                      <span className="block text-xs text-red-600 dark:text-red-400">{b.reasons.join(" · ")}</span>
                    </li>
                  ))}
                  {preview.blocked.length === 0 && <li className="text-dim">Nothing blocked.</li>}
                </ul>
              </div>
            </div>
            <div className="flex items-center gap-3 border-t border-border pt-4 dark:border-neutral-800">
              <button
                disabled={preview.moves.length === 0}
                onClick={() => setConfirmed(true)}
                className="btn btn-primary text-sm disabled:opacity-40"
              >
                Confirm transfer of {preview.moves.length} item(s)
              </button>
              {confirmed && (
                <span className="text-sm text-emerald-700 dark:text-emerald-400">
                  Confirmed in demo — no real memory moved.
                </span>
              )}
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
