"use client";

import { useState } from "react";
import { Icon, Badge, RiskChip, StatusLabel, cn } from "@ronin/design-system";
import {
  DISCLOSURE,
  BLOCKED_CAPABILITIES,
  EMERGENCY_TERMS,
  EMERGENCY_GUIDANCE,
  TOPIC,
  SOURCES,
  PHI_NOTE,
  SAMPLE_QUESTIONS,
} from "./_data";

function isEmergency(q: string): boolean {
  const s = q.toLowerCase();
  return EMERGENCY_TERMS.some((t) => s.includes(t));
}

export default function RoninHealthcare() {
  const [query, setQuery] = useState("");
  const [activeSource, setActiveSource] = useState<string | null>(null);
  const emergency = isEmergency(query);

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
              className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-[4px] bg-accent-tint px-1 align-super text-[0.625rem] font-semibold text-accent-deep hover:bg-accent hover:text-on-accent"
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
      {/* Unmissable, non-dismissable disclosure (fail-closed) */}
      <div className="mb-5 flex items-start gap-3 rounded-xl border border-warn/40 bg-warn-soft px-4 py-3">
        <Icon name="shield" size={18} className="mt-0.5 shrink-0 text-warn" />
        <p className="text-[0.8125rem] leading-relaxed text-text">{DISCLOSURE}</p>
      </div>

      <div className="mb-6 flex flex-wrap items-center gap-2">
        <Badge tone="accent"><Icon name="healthcare" size={12} /> Healthcare Information</Badge>
        <RiskChip tone="caution" label="Non-diagnostic" />
        <StatusLabel kind="IMPLEMENTED" detail="offline sample" />
      </div>

      {/* Ask bar with live emergency detection */}
      <div className={cn(
        "mb-2 flex items-center gap-2 rounded-xl border px-4 py-3 transition-colors",
        emergency ? "border-danger bg-danger-soft" : "border-border bg-surface",
      )}>
        <Icon name={emergency ? "alert" : "healthcare"} size={18} className={emergency ? "text-danger" : "text-accent"} />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask a health question — Ronin gives information, never a diagnosis…"
          className="flex-1 bg-transparent text-[0.9375rem] text-text placeholder:text-text-faint focus:outline-none"
        />
        <button
          disabled={emergency}
          className="rounded-lg bg-accent px-4 py-1.5 text-[0.8125rem] font-medium text-on-accent hover:bg-accent-deep disabled:opacity-40"
        >
          Ask
        </button>
      </div>
      <p className="mb-6 text-[0.6875rem] text-text-faint">
        Try typing “chest pain” to see the fail-closed emergency response (demo).
      </p>

      {emergency ? (
        <div className="rounded-2xl border-2 border-danger bg-danger-soft p-6">
          <div className="mb-2 flex items-center gap-2">
            <Icon name="alert" size={22} className="text-danger" />
            <h2 className="text-[1.25rem] font-semibold text-danger">Possible emergency — Ronin stops here</h2>
          </div>
          <p className="text-[0.9375rem] leading-relaxed text-text">{EMERGENCY_GUIDANCE}</p>
          <p className="mt-3 text-[0.75rem] text-text-dim">
            Ronin will not attempt to assess or manage an emergency. This is the fail-closed behavior by design.
          </p>
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
          {/* Information summary */}
          <div className="min-w-0 space-y-8">
            <section>
              <h1 className="text-[1.5rem] font-semibold tracking-tight text-text">{TOPIC.title}</h1>
              <p className="mt-1 text-[0.875rem] text-text-dim">{TOPIC.subtitle}</p>

              <div className="mt-4 space-y-2.5">
                {TOPIC.keyPoints.map((kp, i) => (
                  <div key={i} className="flex items-start gap-2.5 rounded-lg border border-border bg-surface p-3.5">
                    <Icon name="dot" size={14} className="mt-1 shrink-0 text-accent" />
                    <p className="text-[0.9375rem] leading-relaxed text-text">
                      {kp.text}
                      {kp.cite && <Cite ns={kp.cite} />}
                    </p>
                  </div>
                ))}
              </div>

              <div className="mt-4 flex items-start gap-3 rounded-xl border border-info/40 bg-info-soft p-4">
                <Icon name="shield" size={16} className="mt-0.5 shrink-0 text-info" />
                <div>
                  <p className="text-[0.8125rem] font-semibold text-text">What this isn't</p>
                  <p className="mt-0.5 text-[0.8125rem] leading-relaxed text-text-dim">{TOPIC.notAdvice}</p>
                </div>
              </div>
            </section>

            {/* Blocked capabilities */}
            <section>
              <h2 className="mb-3 flex items-center gap-2 text-[0.75rem] font-semibold uppercase tracking-wider text-text-faint">
                <Icon name="shield" size={14} /> What Ronin will not do here
              </h2>
              <div className="overflow-hidden rounded-xl border border-border bg-surface">
                {BLOCKED_CAPABILITIES.map((c, i) => (
                  <div key={i} className={cn("flex items-start gap-3 p-3.5", i > 0 && "border-t border-border")}>
                    <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-danger-soft text-danger">
                      <Icon name="alert" size={12} />
                    </span>
                    <div>
                      <p className="text-[0.875rem] font-medium text-text">{c.label}</p>
                      <p className="text-[0.75rem] text-text-dim">{c.why}</p>
                    </div>
                    <span className="ml-auto"><RiskChip tone="restricted" label="Blocked" /></span>
                  </div>
                ))}
              </div>
            </section>

            {/* PHI note */}
            <div className="flex items-start gap-3 rounded-xl border border-border bg-surface-sunken p-4">
              <Icon name="vault" size={16} className="mt-0.5 shrink-0 text-accent" />
              <p className="text-[0.8125rem] leading-relaxed text-text-dim">{PHI_NOTE}</p>
            </div>
          </div>

          {/* Sources + sample questions */}
          <aside className="space-y-4">
            <div>
              <h2 className="mb-2 flex items-center justify-between text-[0.75rem] font-semibold uppercase tracking-wider text-text-faint">
                Sources <RiskChip tone="review" label="Sample" />
              </h2>
              <div className="space-y-2">
                {SOURCES.map((s) => (
                  <div
                    key={s.id}
                    onClick={() => setActiveSource(activeSource === s.id ? null : s.id)}
                    className={cn(
                      "cursor-pointer rounded-xl border bg-surface p-3 transition-all",
                      activeSource === s.id ? "border-accent shadow-[0_4px_16px_rgba(26,24,22,0.08)]" : "border-border hover:border-border-strong",
                    )}
                  >
                    <div className="mb-1 flex items-center gap-2">
                      <span className="grid size-5 shrink-0 place-items-center rounded bg-accent text-[0.625rem] font-semibold text-on-accent">{s.n}</span>
                      <span className="truncate text-[0.75rem] text-text-faint">{s.domain}</span>
                      <Badge tone="success">{s.reliability}</Badge>
                    </div>
                    <p className="text-[0.8125rem] font-medium leading-snug text-text">{s.title}</p>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <h2 className="mb-2 text-[0.75rem] font-semibold uppercase tracking-wider text-text-faint">Try asking</h2>
              <div className="space-y-2">
                {SAMPLE_QUESTIONS.map((q) => (
                  <button
                    key={q}
                    onClick={() => setQuery(q)}
                    className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-left text-[0.8125rem] text-text-dim transition-colors hover:border-border-strong hover:text-text"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
