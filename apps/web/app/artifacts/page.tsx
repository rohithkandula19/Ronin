"use client";

import { useMemo, useState } from "react";
import {
  DEMO_ARTIFACTS,
  canRestore,
  latestVersion,
  provenanceLinks,
  typeBadge,
  versionDiffSummary,
} from "../../lib/artifacts_ui.mjs";

const TONE: Record<string, string> = {
  danger: "bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-300 dark:border-red-900",
  warn: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900",
  ok: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-900",
  info: "bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950/40 dark:text-sky-300 dark:border-sky-900",
  neutral: "bg-neutral-100 text-neutral-600 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:border-neutral-700",
};

type Artifact = (typeof DEMO_ARTIFACTS)[number];
type Version = Artifact["versions"][number];

function fmtDate(iso: string) {
  try {
    return new Date(iso).toISOString().slice(0, 10);
  } catch {
    return iso;
  }
}

export default function ArtifactsPage() {
  const [artifacts] = useState<Artifact[]>(DEMO_ARTIFACTS);
  const [selectedId, setSelectedId] = useState<string>(DEMO_ARTIFACTS[0].id);
  const [restoredNote, setRestoredNote] = useState<string>("");

  const selected = useMemo(
    () => artifacts.find((a) => a.id === selectedId) ?? artifacts[0],
    [artifacts, selectedId]
  );
  const latest = latestVersion(selected) as Version | null;
  const prov = provenanceLinks(selected);

  // Compare latest against the previous version, if any.
  const versions = selected.versions;
  const prev = versions.length > 1 ? versions[versions.length - 2] : null;
  const diff = prev && latest ? versionDiffSummary(prev, latest) : null;

  return (
    <main className="mx-auto max-w-5xl px-6 py-10 text-ink dark:text-neutral-100">
      <header className="mb-8">
        <p className="text-sm font-medium text-dim">Ronin AI OS</p>
        <h1 className="text-3xl font-bold tracking-tight">Artifacts</h1>
        <p className="mt-2 max-w-2xl text-dim">
          Structured outputs Ronin produced — reports, plans, code, diagrams and more. Each is
          stored as structured content (typed blocks and fields), not raw HTML, so versions can
          be compared field-by-field and restored. Every artifact links back to its provenance.
        </p>
        <p className="mt-3 inline-block rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
          Demo mode — artifacts are labeled fixtures. Live artifacts load from <code>/api/v1</code> at runtime.
        </p>
      </header>

      <div className="grid gap-6 md:grid-cols-[minmax(0,18rem)_1fr]">
        {/* List by type */}
        <aside>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-dim">Artifacts</h2>
          <ul className="space-y-2">
            {artifacts.map((a) => {
              const badge = typeBadge(a.type);
              const active = a.id === selected.id;
              return (
                <li key={a.id}>
                  <button
                    onClick={() => { setSelectedId(a.id); setRestoredNote(""); }}
                    className={`w-full rounded-lg border p-3 text-left focus:outline-none focus:ring-2 focus:ring-accent ${
                      active
                        ? "border-accent bg-accent/5 dark:bg-accent/10"
                        : "border-border bg-white hover:bg-bg dark:border-neutral-800 dark:bg-neutral-900 dark:hover:bg-neutral-800"
                    }`}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="font-medium">{a.title}</span>
                      <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${TONE[badge.tone]}`}>
                        <span aria-hidden>{badge.glyph}</span> {badge.label}
                      </span>
                    </span>
                    <span className="mt-1 block text-xs text-dim">{a.versions.length} version(s)</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>

        {/* Detail */}
        <section>
          <div className="rounded-xl border border-border bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-xl font-semibold">{selected.title}</h2>
                <p className="mt-0.5 text-sm text-dim">{typeBadge(selected.type).label}
                  {selected.locked && " · locked (approved / signed)"}</p>
              </div>
            </div>

            {/* Provenance */}
            <div className="mt-4 grid gap-2 border-t border-border pt-4 text-sm dark:border-neutral-800">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-dim">Provenance</h3>
              <div className="text-dim"><span className="font-medium text-ink dark:text-neutral-100">Conversation:</span>{" "}
                {prov.conversation ? prov.conversation.id : "—"}</div>
              <div className="text-dim"><span className="font-medium text-ink dark:text-neutral-100">Model:</span> {prov.model ?? "—"}
                {prov.adapter ? ` · adapter ${prov.adapter}` : ""}</div>
              <div className="text-dim"><span className="font-medium text-ink dark:text-neutral-100">Sources:</span>{" "}
                {prov.sources.length > 0 ? prov.sources.map((s: { label: string }) => s.label).join(", ") : "—"}</div>
              <div className="text-dim"><span className="font-medium text-ink dark:text-neutral-100">Approvals:</span>{" "}
                {prov.approvals.length > 0
                  ? prov.approvals.map((ap: { by: string }) => ap.by).join(", ")
                  : "None"}</div>
            </div>

            {/* Structured content of latest version */}
            {latest && (
              <div className="mt-4 border-t border-border pt-4 dark:border-neutral-800">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-dim">
                  Latest content (v{String((latest as { number?: number }).number ?? "?")}) — structured blocks
                </h3>
                <dl className="space-y-1 text-sm">
                  {latest.content.map((block: { key: string; value: unknown }) => (
                    <div key={block.key} className="flex gap-2">
                      <dt className="min-w-[7rem] font-mono text-xs text-dim">{block.key}</dt>
                      <dd className="font-mono text-xs">{JSON.stringify(block.value)}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}

            {/* Version history + compare/restore */}
            <div className="mt-4 border-t border-border pt-4 dark:border-neutral-800">
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-dim">Version history</h3>
              {diff && (
                <p className="mb-3 rounded-md bg-bg px-3 py-2 text-xs text-dim dark:bg-neutral-800">
                  Latest vs previous: {diff.summary}
                  {diff.added.length > 0 && <> · added: {diff.added.join(", ")}</>}
                  {diff.changed.length > 0 && <> · changed: {diff.changed.join(", ")}</>}
                  {diff.removed.length > 0 && <> · removed: {diff.removed.join(", ")}</>}
                </p>
              )}
              <ul className="space-y-2">
                {versions.map((v) => {
                  const isLatest = latest && v.id === latest.id;
                  const restorable = canRestore(selected, v.id);
                  return (
                    <li key={v.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm dark:border-neutral-800">
                      <span className="font-mono text-xs">v{String((v as { number?: number }).number ?? "?")}</span>
                      <span className="text-dim">{fmtDate(v.created_at)}</span>
                      <span className="text-dim">by {v.author}</span>
                      {isLatest && <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300">Current</span>}
                      <span className="ml-auto">
                        {restorable ? (
                          <button
                            onClick={() => setRestoredNote(`Restored v${String((v as { number?: number }).number)} in demo — no real change applied.`)}
                            className="btn btn-secondary text-xs"
                          >
                            Restore
                          </button>
                        ) : (
                          <span className="text-xs text-neutral-400">
                            {selected.locked ? "Locked" : isLatest ? "Current" : "—"}
                          </span>
                        )}
                      </span>
                    </li>
                  );
                })}
              </ul>
              {restoredNote && <p className="mt-2 text-xs text-emerald-700 dark:text-emerald-400">{restoredNote}</p>}
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
