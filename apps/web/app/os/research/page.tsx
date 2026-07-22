"use client";

import { useState } from "react";
import { Icon, Badge, RiskChip, StatusLabel, cn, type IconName } from "@ronin/design-system";
import {
  QUESTION,
  RESEARCH_STATUS,
  SOURCES,
  SYNTHESIS,
  CLAIMS,
  SUGGESTED_FOLLOWUPS,
  type Source,
  type Reliability,
  type Support,
} from "./_data";

const TYPE_ICON: Record<Source["type"], IconName> = {
  paper: "science",
  article: "knowledge",
  docs: "artifacts",
  forum: "agents",
  dataset: "finance",
};

const RELIABILITY: Record<Reliability, { label: string; tone: "success" | "info" | "warn" }> = {
  high: { label: "High reliability", tone: "success" },
  medium: { label: "Medium reliability", tone: "info" },
  low: { label: "Low reliability", tone: "warn" },
};

function SupportLabel({ support }: { support: Support }) {
  if (support.kind === "sourced") return <Badge tone="success"><Icon name="check" size={11} /> Sourced</Badge>;
  if (support.kind === "mixed") return <Badge tone="info"><Icon name="knowledge" size={11} /> Partly sourced</Badge>;
  return <Badge tone="warn"><Icon name="spark" size={11} /> Inference</Badge>;
}

export default function RoninResearch() {
  const [activeSource, setActiveSource] = useState<string | null>(null);

  const byN = (n: number) => SOURCES.find((s) => s.n === n);

  function Cite({ ns }: { ns: number[] }) {
    return (
      <span className="whitespace-nowrap">
        {ns.map((n) => {
          const src = byN(n);
          return (
            <button
              key={n}
              onClick={() => src && setActiveSource(src.id)}
              className={cn(
                "mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-[4px] px-1 align-super text-[0.625rem] font-semibold transition-colors",
                activeSource === src?.id
                  ? "bg-accent text-on-accent"
                  : "bg-accent-tint text-accent-deep hover:bg-accent hover:text-on-accent",
              )}
              title={src?.title}
            >
              {n}
            </button>
          );
        })}
      </span>
    );
  }

  return (
    <div className="mx-auto max-w-app px-6 py-8 sm:px-10">
      {/* Ask bar */}
      <div className="mb-6 flex items-center gap-2 rounded-xl border border-border bg-surface px-4 py-3">
        <Icon name="research" size={18} className="text-accent" />
        <input
          defaultValue=""
          placeholder="Ask a research question — Ronin finds sources first, then synthesizes…"
          className="flex-1 bg-transparent text-[0.9375rem] text-text placeholder:text-text-faint focus:outline-none"
        />
        <button className="rounded-lg bg-accent px-4 py-1.5 text-[0.8125rem] font-medium text-on-accent hover:bg-accent-deep">
          Research
        </button>
      </div>

      {/* Question header */}
      <div className="mb-6">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Badge tone="accent"><Icon name="research" size={12} /> Research</Badge>
          <StatusLabel kind={RESEARCH_STATUS === "synthesized" ? "IMPLEMENTED" : "DRAFT"} detail={RESEARCH_STATUS} />
          <span className="text-[0.8125rem] text-text-faint">{SOURCES.length} sources</span>
        </div>
        <h1 className="max-w-3xl text-[1.75rem] font-semibold leading-tight tracking-tight text-text">
          {QUESTION}
        </h1>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        {/* Synthesis + claims */}
        <div className="min-w-0 space-y-8">
          <section>
            <h2 className="mb-3 flex items-center gap-2 text-[0.75rem] font-semibold uppercase tracking-wider text-text-faint">
              <Icon name="spark" size={14} /> Synthesis
            </h2>
            <div className="space-y-4 text-[1rem] leading-[1.7] text-text">
              {SYNTHESIS.map((p, i) => (
                <p key={i}>
                  {p.runs.map((r, j) =>
                    "cite" in r ? <Cite key={j} ns={r.cite} /> : <span key={j}>{r.text}</span>,
                  )}
                </p>
              ))}
            </div>
          </section>

          <section>
            <h2 className="mb-3 flex items-center gap-2 text-[0.75rem] font-semibold uppercase tracking-wider text-text-faint">
              <Icon name="check" size={14} /> Key claims &amp; support
            </h2>
            <div className="overflow-hidden rounded-xl border border-border bg-surface">
              {CLAIMS.map((c, i) => (
                <div key={i} className={cn("flex items-start gap-3 p-4", i > 0 && "border-t border-border")}>
                  <span className="min-w-0 flex-1 text-[0.9375rem] leading-relaxed text-text">{c.text}</span>
                  <div className="flex shrink-0 flex-col items-end gap-1.5">
                    <SupportLabel support={c.support} />
                    {"sourceIds" in c.support && (
                      <div className="flex gap-1">
                        {c.support.sourceIds.map((sid) => {
                          const s = SOURCES.find((x) => x.id === sid)!;
                          return (
                            <button
                              key={sid}
                              onClick={() => setActiveSource(sid)}
                              className="grid size-5 place-items-center rounded bg-accent-tint text-[0.625rem] font-semibold text-accent-deep hover:bg-accent hover:text-on-accent"
                            >
                              {s.n}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-2 flex items-center gap-1.5 text-[0.75rem] text-text-dim">
              <Icon name="shield" size={13} className="text-accent" />
              Claims labelled <b className="font-semibold">Inference</b> are Ronin's reasoning, not drawn from a source.
            </p>
          </section>

          <section>
            <h2 className="mb-3 text-[0.75rem] font-semibold uppercase tracking-wider text-text-faint">Follow up</h2>
            <div className="flex flex-wrap gap-2">
              {SUGGESTED_FOLLOWUPS.map((f) => (
                <button
                  key={f}
                  className="rounded-lg border border-border bg-surface px-3 py-2 text-left text-[0.8125rem] text-text-dim transition-colors hover:border-border-strong hover:text-text"
                >
                  {f}
                </button>
              ))}
            </div>
          </section>
        </div>

        {/* Sources rail */}
        <aside className="space-y-3">
          <h2 className="flex items-center justify-between text-[0.75rem] font-semibold uppercase tracking-wider text-text-faint">
            Sources
            <RiskChip tone="review" label="Sample" />
          </h2>
          {SOURCES.map((s) => {
            const rel = RELIABILITY[s.reliability];
            const active = activeSource === s.id;
            return (
              <div
                key={s.id}
                onClick={() => setActiveSource(active ? null : s.id)}
                className={cn(
                  "cursor-pointer rounded-xl border bg-surface p-3.5 transition-all",
                  active ? "border-accent shadow-[0_4px_16px_rgba(26,24,22,0.08)]" : "border-border hover:border-border-strong",
                )}
              >
                <div className="mb-1.5 flex items-center gap-2">
                  <span className="grid size-5 shrink-0 place-items-center rounded bg-accent text-[0.625rem] font-semibold text-on-accent">
                    {s.n}
                  </span>
                  <Icon name={TYPE_ICON[s.type]} size={14} className="text-text-dim" />
                  <span className="truncate text-[0.75rem] text-text-faint">{s.domain}</span>
                  {s.status === "queued" && (
                    <span className="ml-auto flex items-center gap-1 text-[0.6875rem] text-text-faint">
                      <Icon name="clock" size={11} /> queued
                    </span>
                  )}
                </div>
                <p className="text-[0.8125rem] font-medium leading-snug text-text">{s.title}</p>
                {active && (
                  <div className="mt-2.5 space-y-2 border-t border-border pt-2.5">
                    <Badge tone={rel.tone}>{rel.label}</Badge>
                    <p className="text-[0.75rem] leading-relaxed text-text-dim">{s.note}</p>
                    <p className="flex items-center gap-1 text-[0.6875rem] text-text-faint">
                      <Icon name="shield" size={11} />
                      Sample source — URLs are verified before linking on connected data.
                    </p>
                  </div>
                )}
              </div>
            );
          })}
        </aside>
      </div>
    </div>
  );
}
