"use client";

import { adaptDifficulty, groundedClaim } from "../../../lib/education_world.mjs";
import type { StudyPlan } from "./ArtifactViews";

export interface Source {
  id: string;
  title: string;
  url: string;
  snippet: string;
  tags: string[];
}
export interface Goal {
  id: string;
  label: string;
  done: boolean;
}

const PANEL =
  "rounded-xl border border-border bg-white p-4 dark:border-neutral-700 dark:bg-neutral-900";

// ---- Sources (grounded summary shows source cards) -------------------------

export function SourcesPanel({
  sources,
  summary,
}: {
  sources: Source[];
  summary?: string;
}) {
  const claim = summary ? groundedClaim(summary, sources) : null;
  return (
    <div className={PANEL}>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ink dark:text-neutral-100">Sources</h3>
        {claim && (
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
              claim.status === "GROUNDED"
                ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300"
                : "bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300"
            }`}
            title={
              claim.status === "GROUNDED"
                ? "Summary is supported by a cited source."
                : "No source supports this — treat as inference."
            }
          >
            {claim.status}
          </span>
        )}
      </div>
      {summary && (
        <p className="mb-3 text-xs leading-relaxed text-dim">{summary}</p>
      )}
      <ul className="space-y-2">
        {sources.map((s) => (
          <li
            key={s.id}
            className="rounded-lg border border-border p-2.5 dark:border-neutral-700"
          >
            <a
              href={s.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-medium text-accent hover:underline focus:outline-none focus:ring-2 focus:ring-accent"
            >
              {s.title}
            </a>
            <p className="mt-1 text-[11px] leading-snug text-dim">{s.snippet}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---- Learning goals / progress ---------------------------------------------

export function GoalsPanel({
  plan,
  goals,
  onToggle,
}: {
  plan: StudyPlan;
  goals: Goal[];
  onToggle: (id: string) => void;
}) {
  return (
    <div className={PANEL}>
      <h3 className="mb-1 text-sm font-semibold text-ink dark:text-neutral-100">
        Learning goals
      </h3>
      <p className="mb-2 text-[11px] text-dim">{plan.subject} · {plan.role}</p>
      <ul className="space-y-1.5">
        {goals.map((g) => (
          <li key={g.id}>
            <label className="flex cursor-pointer items-center gap-2 text-xs text-ink dark:text-neutral-200">
              <input
                type="checkbox"
                className="h-3.5 w-3.5 rounded border-border text-accent focus:ring-2 focus:ring-accent"
                checked={g.done}
                onChange={() => onToggle(g.id)}
              />
              <span className={g.done ? "text-dim line-through" : ""}>{g.label}</span>
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---- Difficulty adaptation control -----------------------------------------

export function DifficultyPanel({
  current,
  recentScorePct,
  onChange,
}: {
  current: string;
  recentScorePct: number;
  onChange: (level: string) => void;
}) {
  const levels = ["easy", "medium", "hard"];
  const suggested = adaptDifficulty(current, recentScorePct);
  return (
    <div className={PANEL}>
      <h3 className="mb-1 text-sm font-semibold text-ink dark:text-neutral-100">
        Difficulty
      </h3>
      <p className="mb-2 text-[11px] text-dim">
        Recent score {recentScorePct}% · suggested{" "}
        <span className="font-medium text-ink dark:text-neutral-200">{suggested}</span>
      </p>
      <div
        role="radiogroup"
        aria-label="Difficulty level"
        className="flex gap-1 rounded-lg border border-border p-1 dark:border-neutral-700"
      >
        {levels.map((lvl) => {
          const active = current === lvl;
          return (
            <button
              key={lvl}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onChange(lvl)}
              className={`flex-1 rounded-md px-2 py-1 text-xs capitalize transition focus:outline-none focus:ring-2 focus:ring-accent ${
                active
                  ? "bg-accent text-white"
                  : "text-dim hover:bg-bg dark:hover:bg-neutral-800"
              }`}
            >
              {lvl}
            </button>
          );
        })}
      </div>
      {suggested !== current && (
        <button
          type="button"
          onClick={() => onChange(suggested)}
          className="mt-2 text-[11px] text-accent hover:underline focus:outline-none focus:ring-2 focus:ring-accent"
        >
          Adapt to {suggested} →
        </button>
      )}
    </div>
  );
}
