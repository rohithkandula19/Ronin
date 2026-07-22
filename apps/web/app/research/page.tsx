"use client";

import { useMemo, useState } from "react";
import {
  freshnessLabel,
  sourceQuality,
  hasFabricatedUrl,
  claimStatus,
  citationCoverage,
  detectContradiction,
  groundingNotice,
  DEMO_SOURCES,
  DEMO_CLAIMS,
  DEMO_QUERIES,
} from "../../lib/research_ui.mjs";

// Fixed "now" so the demo's freshness labels are stable and reproducible.
const NOW = new Date("2026-07-22T00:00:00Z");

const TONE: Record<string, string> = {
  ok: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-900",
  warn: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900",
  danger: "bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-300 dark:border-red-900",
  neutral: "bg-neutral-100 text-neutral-600 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:border-neutral-700",
};

const STATUS_TONE: Record<string, string> = {
  supported: "ok",
  contradicted: "danger",
  inference: "warn",
};

const TIER_TONE: Record<string, string> = {
  high: "ok",
  medium: "neutral",
  low: "danger",
};

const STAGE_LABEL: Record<string, string> = {
  seed: "Seed",
  expand: "Expand",
  verify: "Verify",
};

function Badge({ tone, children }: { tone: string; children: React.ReactNode }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-xs font-medium ${
        TONE[tone] ?? TONE.neutral
      }`}
    >
      {children}
    </span>
  );
}

function Panel({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-border bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="mb-4">
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        {subtitle && <p className="mt-1 text-sm text-dim dark:text-neutral-400">{subtitle}</p>}
      </div>
      {children}
    </section>
  );
}

export default function ResearchNotebook() {
  // Demo mode: retrieval is off, so the UI must be explicit that answers rest
  // on model knowledge, not live sources.
  const [retrievalAvailable, setRetrievalAvailable] = useState(false);

  const notice = useMemo(() => groundingNotice(retrievalAvailable), [retrievalAvailable]);
  const coverage = useMemo(() => citationCoverage(DEMO_CLAIMS), []);

  const sourceById = useMemo(() => {
    const m = new Map<string, (typeof DEMO_SOURCES)[number]>();
    for (const s of DEMO_SOURCES) m.set(s.id, s);
    return m;
  }, []);

  const collected = DEMO_SOURCES.filter((s) => s.accepted);
  const rejected = DEMO_SOURCES.filter((s) => !s.accepted);

  // Every contradicting pair across the demo claims, for the conflict panel.
  const contradictions = useMemo(() => {
    const out: { a: (typeof DEMO_CLAIMS)[number]; b: (typeof DEMO_CLAIMS)[number]; reason: string }[] = [];
    for (let i = 0; i < DEMO_CLAIMS.length; i++) {
      for (let j = i + 1; j < DEMO_CLAIMS.length; j++) {
        const res = detectContradiction(DEMO_CLAIMS[i], DEMO_CLAIMS[j]);
        if (res.contradiction) out.push({ a: DEMO_CLAIMS[i], b: DEMO_CLAIMS[j], reason: res.reason });
      }
    }
    return out;
  }, []);

  const report = useMemo(() => buildReport(retrievalAvailable), [retrievalAvailable]);
  const [copied, setCopied] = useState(false);

  async function copyReport() {
    try {
      await navigator.clipboard.writeText(report);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="min-h-screen bg-bg text-ink dark:bg-neutral-950 dark:text-neutral-100">
      <main className="mx-auto max-w-5xl px-6 py-10">
        <header className="mb-6">
          <p className="text-sm font-medium text-dim dark:text-neutral-400">Ronin AI OS</p>
          <h1 className="text-3xl font-bold tracking-tight">Research notebook</h1>
          <p className="mt-2 max-w-2xl text-dim dark:text-neutral-400">
            Source-first research. Every claim is traced to the evidence behind it, sourced
            facts are kept visibly separate from model inference, and citations are never
            fabricated.
          </p>
        </header>

        {/* Grounding / honesty banner */}
        <div
          role="status"
          className={`mb-6 rounded-2xl border p-4 ${TONE[notice.tone]}`}
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="font-semibold">{notice.title}</p>
              <p className="mt-1 text-sm opacity-90">{notice.notice}</p>
            </div>
            <label className="flex shrink-0 items-center gap-2 text-sm font-medium">
              <input
                type="checkbox"
                checked={retrievalAvailable}
                onChange={(e) => setRetrievalAvailable(e.target.checked)}
                className="h-4 w-4 accent-accent"
              />
              Simulate live retrieval
            </label>
          </div>
        </div>

        {/* Coverage summary */}
        <div className="mb-6 grid gap-4 sm:grid-cols-3">
          <Stat label="Citation coverage" value={`${coverage.percent}%`} hint={`${coverage.covered}/${coverage.total} claims sourced`} />
          <Stat label="Sources collected" value={String(collected.length)} hint={`${rejected.length} rejected`} />
          <Stat label="Open conflicts" value={String(contradictions.length)} hint="conflicting-evidence pairs" />
        </div>

        <div className="space-y-6">
          {/* 1. Search planning */}
          <Panel title="Search plan" subtitle="Queries by stage, with the rationale for each.">
            <ol className="space-y-3">
              {DEMO_QUERIES.map((q) => (
                <li
                  key={q.id}
                  className="flex flex-wrap items-start gap-3 rounded-xl border border-border p-3 dark:border-neutral-800"
                >
                  <Badge tone="neutral">{STAGE_LABEL[q.stage] ?? q.stage}</Badge>
                  <div className="min-w-[200px] flex-1">
                    <code className="font-mono text-sm">{q.query}</code>
                    <p className="mt-1 text-xs text-dim dark:text-neutral-400">{q.rationale}</p>
                  </div>
                  <Badge tone={q.status === "done" ? "ok" : "warn"}>
                    {q.status === "done" ? "done" : "running"}
                  </Badge>
                </li>
              ))}
            </ol>
          </Panel>

          {/* 2. Sources collected */}
          <Panel title="Sources collected" subtitle="Accepted into the evidence set.">
            <ul className="grid gap-3 sm:grid-cols-2">
              {collected.map((s) => (
                <SourceCard key={s.id} source={s} />
              ))}
            </ul>
          </Panel>

          {/* 3. Sources rejected */}
          <Panel title="Sources rejected" subtitle="Excluded from citations, with the reason why.">
            <ul className="space-y-3">
              {rejected.map((s) => (
                <li
                  key={s.id}
                  className="rounded-xl border border-border p-3 opacity-90 dark:border-neutral-800"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium line-through decoration-neutral-300">{s.title}</span>
                    <span className="font-mono text-xs text-dim dark:text-neutral-400">{s.domain}</span>
                  </div>
                  <p className="mt-1 text-sm text-red-700 dark:text-red-300">Rejected: {s.rejectedReason}</p>
                </li>
              ))}
            </ul>
          </Panel>

          {/* 4. Claim -> source mapping */}
          <Panel
            title="Claims and their evidence"
            subtitle="Each claim shows support strength and source dates. Unsupported claims are labelled INFERENCE — model reasoning, not a sourced fact."
          >
            <ul className="space-y-4">
              {DEMO_CLAIMS.map((c) => {
                const st = claimStatus(c);
                return (
                  <li
                    key={c.id}
                    className="rounded-xl border border-border p-4 dark:border-neutral-800"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <p className="min-w-[220px] flex-1 font-medium">{c.text}</p>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        <Badge tone={STATUS_TONE[st.status]}>{st.label}</Badge>
                        {!st.isInference && (
                          <span className="text-xs text-dim dark:text-neutral-400">
                            support: {st.strength}
                          </span>
                        )}
                      </div>
                    </div>

                    {st.isInference ? (
                      <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
                        No source backs this claim. It is model inference and must be verified
                        before being treated as fact.
                      </p>
                    ) : (
                      <ul className="mt-3 space-y-2">
                        {c.sources.map((ref, i) => {
                          const src = sourceById.get(ref.sourceId);
                          if (!src) return null;
                          const fresh = freshnessLabel(src.date, NOW);
                          const refutes = ref.stance === "refutes";
                          return (
                            <li
                              key={`${c.id}-${ref.sourceId}-${i}`}
                              className="flex flex-wrap items-center gap-2 text-sm"
                            >
                              <Badge tone={refutes ? "danger" : "ok"}>
                                {refutes ? "refutes" : "supports"}
                              </Badge>
                              <CiteLink source={src} />
                              <span className="text-xs text-dim dark:text-neutral-400">
                                {src.date}
                              </span>
                              <Badge tone={fresh.tone}>{fresh.label}</Badge>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          </Panel>

          {/* 5. Contradictions */}
          <Panel
            title="Conflicting evidence"
            subtitle="Claims that assert opposing conclusions on the same topic."
          >
            {contradictions.length === 0 ? (
              <p className="text-sm text-dim dark:text-neutral-400">No conflicts detected.</p>
            ) : (
              <ul className="space-y-3">
                {contradictions.map((pair, i) => (
                  <li
                    key={i}
                    className="rounded-xl border border-red-200 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950/30"
                  >
                    <p className="text-sm">
                      <span className="font-medium">A.</span> {pair.a.text}
                    </p>
                    <p className="mt-1 text-sm">
                      <span className="font-medium">B.</span> {pair.b.text}
                    </p>
                    <p className="mt-2 text-xs text-red-700 dark:text-red-300">Conflict: {pair.reason}</p>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          {/* 6. Exportable report */}
          <Panel
            title="Exportable report"
            subtitle="Markdown you can copy out. Inference is flagged; nothing is dressed up as a source it does not have."
          >
            <div className="mb-3 flex justify-end">
              <button
                onClick={copyReport}
                className="btn btn-secondary text-sm dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-100 dark:hover:bg-neutral-700"
              >
                {copied ? "Copied" : "Copy Markdown"}
              </button>
            </div>
            <pre className="max-h-96 overflow-auto rounded-xl border border-border bg-bg p-4 font-mono text-xs leading-relaxed dark:border-neutral-800 dark:bg-neutral-950">
              {report}
            </pre>
          </Panel>
        </div>

        <p className="mt-8 text-center text-xs text-dim dark:text-neutral-500">
          Demo fixtures — labelled example data, realistic https URLs only. This notebook
          never invents citations.
        </p>
      </main>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="rounded-2xl border border-border bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <p className="text-xs font-medium uppercase tracking-wide text-dim dark:text-neutral-400">{label}</p>
      <p className="mt-1 text-2xl font-bold tracking-tight">{value}</p>
      <p className="text-xs text-dim dark:text-neutral-400">{hint}</p>
    </div>
  );
}

function SourceCard({ source }: { source: (typeof DEMO_SOURCES)[number] }) {
  const quality = sourceQuality(source);
  const fresh = freshnessLabel(source.date, NOW);
  return (
    <li className="rounded-xl border border-border p-3 dark:border-neutral-800">
      <div className="flex items-start justify-between gap-2">
        <CiteLink source={source} strong />
        <Badge tone={TIER_TONE[quality.tier]}>{quality.label}</Badge>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-dim dark:text-neutral-400">
        <span className="font-mono">{source.domain}</span>
        <span>·</span>
        <span>{source.date}</span>
        <Badge tone={fresh.tone}>{fresh.label}</Badge>
      </div>
    </li>
  );
}

// Renders a citation link — but REFUSES to link a URL that fails the fabricated
// -URL guard, so the UI can never present a made-up source as real.
function CiteLink({ source, strong }: { source: (typeof DEMO_SOURCES)[number]; strong?: boolean }) {
  const cls = strong ? "font-medium" : "";
  if (hasFabricatedUrl(source)) {
    return (
      <span className={`${cls} text-dim dark:text-neutral-500`} title="Unverifiable URL — not rendered as a link">
        {source.title} (unverifiable source)
      </span>
    );
  }
  return (
    <a
      href={source.url}
      target="_blank"
      rel="noopener noreferrer"
      className={`${cls} text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent`}
    >
      {source.title}
    </a>
  );
}

// Build the export report from the demo data. Kept in the client component
// (uses the same pure helpers) so the copy button always matches what is shown.
function buildReport(retrievalAvailable: boolean): string {
  const notice = groundingNotice(retrievalAvailable);
  const coverage = citationCoverage(DEMO_CLAIMS);
  const lines: string[] = [];
  lines.push("# Research report (DEMO)");
  lines.push("");
  lines.push(`> ${notice.title}: ${notice.notice}`);
  lines.push("");
  lines.push(`Citation coverage: ${coverage.percent}% (${coverage.covered}/${coverage.total} claims sourced)`);
  lines.push("");
  lines.push("## Findings");
  for (const c of DEMO_CLAIMS) {
    const st = claimStatus(c);
    lines.push(`- [${st.label}] ${c.text}`);
    if (st.isInference) {
      lines.push("    - Model inference — no source. Verify before use.");
    } else {
      for (const ref of c.sources) {
        const src = DEMO_SOURCES.find((s) => s.id === ref.sourceId);
        if (!src) continue;
        const url = hasFabricatedUrl(src) ? "(unverifiable URL withheld)" : src.url;
        lines.push(`    - ${ref.stance}: ${src.title} — ${url} (${src.date})`);
      }
    }
  }
  lines.push("");
  lines.push("_All data above is labelled demo content; URLs use reserved example domains._");
  return lines.join("\n");
}
