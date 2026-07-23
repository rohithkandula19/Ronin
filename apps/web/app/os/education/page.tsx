"use client";

import { useState } from "react";
import { Icon, Badge, RiskChip, StatusLabel, cn } from "@ronin/design-system";
import {
  ROLES,
  INTEGRITY_TERMS,
  INTEGRITY_GUIDANCE,
  TOPIC,
  STUDY_PLAN,
  QUIZ,
  SOURCES,
  SAMPLE_PROMPTS,
  type Role,
  type PlanItem,
} from "./_data";

function isIntegrityRisk(q: string): boolean {
  const s = q.toLowerCase();
  return INTEGRITY_TERMS.some((t) => s.includes(t));
}

const PLAN_TONE: Record<PlanItem["state"], string> = {
  due: "bg-warn",
  soon: "bg-info",
  solid: "bg-success",
};

export default function RoninEducation() {
  const [role, setRole] = useState<Role>("student");
  const [query, setQuery] = useState("");
  const [revealed, setRevealed] = useState<Record<number, boolean>>({});
  const [activeSource, setActiveSource] = useState<string | null>(null);
  const integrity = isIntegrityRisk(query);

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
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <Badge tone="accent"><Icon name="education" size={12} /> Education</Badge>
        <RiskChip tone="safe" />
        <StatusLabel kind="IMPLEMENTED" detail="offline sample" />
        {/* role selector */}
        <div className="ml-auto flex overflow-hidden rounded-lg border border-border">
          {ROLES.map((r) => (
            <button
              key={r.id}
              onClick={() => setRole(r.id)}
              title={r.note}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 text-[0.8125rem] transition-colors",
                role === r.id ? "bg-accent/12 text-text" : "text-text-dim hover:text-text",
              )}
            >
              <Icon name={r.icon} size={14} />
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {/* Ask bar with live integrity detection */}
      <div className={cn(
        "mb-2 flex items-center gap-2 rounded-xl border px-4 py-3 transition-colors",
        integrity ? "border-warn bg-warn-soft" : "border-border bg-surface",
      )}>
        <Icon name={integrity ? "shield" : "education"} size={18} className={integrity ? "text-warn" : "text-accent"} />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask to learn something — Ronin teaches, it won't do your graded work…"
          className="flex-1 bg-transparent text-[0.9375rem] text-text placeholder:text-text-faint focus:outline-none"
        />
        <button className="rounded-lg bg-accent px-4 py-1.5 text-[0.8125rem] font-medium text-on-accent hover:bg-accent-deep">
          {integrity ? "Coach me" : "Learn"}
        </button>
      </div>
      <p className="mb-6 text-[0.6875rem] text-text-faint">
        Try “answers to the exam” to see the academic-integrity guardrail (demo).
      </p>

      {integrity ? (
        <div className="rounded-2xl border-2 border-warn bg-warn-soft p-6">
          <div className="mb-2 flex items-center gap-2">
            <Icon name="shield" size={22} className="text-warn" />
            <h2 className="text-[1.25rem] font-semibold text-text">Ronin helps you learn, not cheat</h2>
          </div>
          <p className="text-[0.9375rem] leading-relaxed text-text">{INTEGRITY_GUIDANCE}</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button onClick={() => setQuery("")} className="rounded-lg bg-accent px-4 py-2 text-[0.8125rem] font-medium text-on-accent hover:bg-accent-deep">Explain the concept</button>
            <button onClick={() => setQuery("")} className="rounded-lg border border-border bg-surface px-4 py-2 text-[0.8125rem] font-medium text-text hover:bg-surface-sunken">Walk a similar example</button>
            <button onClick={() => setQuery("")} className="rounded-lg border border-border bg-surface px-4 py-2 text-[0.8125rem] font-medium text-text hover:bg-surface-sunken">Build a practice set</button>
          </div>
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
          <div className="min-w-0 space-y-8">
            {/* Grounded explanation */}
            <section>
              <span className="text-[0.75rem] font-medium uppercase tracking-wider text-text-faint">{TOPIC.subject}</span>
              <h1 className="mt-1 text-[1.5rem] font-semibold tracking-tight text-text">{TOPIC.title}</h1>
              <div className="mt-4 space-y-2.5">
                {TOPIC.keyPoints.map((kp, i) => (
                  <div key={i} className="flex items-start gap-2.5 rounded-lg border border-border bg-surface p-3.5">
                    <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-accent-tint text-[0.6875rem] font-semibold text-accent-deep">{i + 1}</span>
                    <p className="text-[0.9375rem] leading-relaxed text-text">
                      {kp.text}
                      {kp.cite && <Cite ns={kp.cite} />}
                    </p>
                  </div>
                ))}
              </div>
              <div className="mt-4 flex items-start gap-3 rounded-xl border border-accent-soft/40 bg-accent-tint/50 p-4">
                <Icon name="spark" size={16} className="mt-0.5 shrink-0 text-accent-deep" />
                <div>
                  <p className="text-[0.8125rem] font-semibold text-text">Check yourself</p>
                  <p className="mt-0.5 text-[0.8125rem] leading-relaxed text-text-dim">{TOPIC.checkYourself}</p>
                </div>
              </div>
            </section>

            {/* Self-check practice */}
            <section>
              <h2 className="mb-3 flex items-center gap-2 text-[0.75rem] font-semibold uppercase tracking-wider text-text-faint">
                <Icon name="check" size={14} /> Practice (self-check, not graded)
              </h2>
              <div className="space-y-2">
                {QUIZ.map((item, i) => (
                  <div key={i} className="rounded-xl border border-border bg-surface p-4">
                    <p className="text-[0.9375rem] font-medium text-text">{i + 1}. {item.q}</p>
                    {revealed[i] ? (
                      <p className="mt-2 rounded-lg bg-success-soft px-3 py-2 text-[0.875rem] text-text">{item.a}</p>
                    ) : (
                      <button
                        onClick={() => setRevealed((r) => ({ ...r, [i]: true }))}
                        className="mt-2 text-[0.8125rem] font-medium text-accent hover:underline"
                      >
                        Reveal answer
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </section>
          </div>

          {/* Study plan + sources */}
          <aside className="space-y-6">
            <div>
              <h2 className="mb-2 flex items-center gap-2 text-[0.75rem] font-semibold uppercase tracking-wider text-text-faint">
                <Icon name="clock" size={14} /> Study plan
              </h2>
              <div className="space-y-2">
                {STUDY_PLAN.map((p, i) => (
                  <div key={i} className="rounded-xl border border-border bg-surface p-3">
                    <div className="mb-1.5 flex items-center justify-between gap-2">
                      <span className="truncate text-[0.8125rem] font-medium text-text">{p.topic}</span>
                      <span className="shrink-0 text-[0.6875rem] text-text-faint">{p.due}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-sunken">
                        <div className={cn("h-full rounded-full", PLAN_TONE[p.state])} style={{ width: `${p.mastery}%` }} />
                      </div>
                      <span className="text-[0.6875rem] tabular-nums text-text-dim">{p.mastery}%</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

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
                {SAMPLE_PROMPTS.map((q) => (
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
