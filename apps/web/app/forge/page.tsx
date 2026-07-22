"use client";

import { useState } from "react";
import {
  DatasetsPanel,
  JobsPanel,
  AdaptersPanel,
  EvalPanel,
} from "./_components/panels";

type TabId = "datasets" | "jobs" | "adapters" | "evaluation";

const TABS: { id: TabId; label: string; blurb: string }[] = [
  { id: "datasets", label: "Datasets", blurb: "Provenance, consent, redaction, splits" },
  { id: "jobs", label: "Training jobs", blurb: "Bundles, config, cost — runs on external GPU" },
  { id: "adapters", label: "Adapters", blurb: "Registry & release lifecycle" },
  { id: "evaluation", label: "Evaluation", blurb: "Suites, deltas, failures" },
];

export default function ForgeStudio() {
  const [tab, setTab] = useState<TabId>("datasets");

  return (
    <main className="mx-auto max-w-6xl px-6 py-10 text-ink dark:text-neutral-100">
      <header className="mb-8">
        <p className="text-sm font-medium text-dim dark:text-neutral-400">
          Ronin AI OS · Forge
        </p>
        <h1 className="text-3xl font-bold tracking-tight">Ronin Forge</h1>
        <p className="mt-2 max-w-2xl text-dim dark:text-neutral-400">
          A studio for building specialized adapters: curate consented data,
          generate training bundles, run them on external GPUs, evaluate, and
          release under human sign-off. This studio never presents a generated
          bundle as a trained model.
        </p>
        <div className="mt-3 inline-flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
          Demo mode · fixtures only · local training is BLOCKED_HARDWARE (no GPU)
        </div>
      </header>

      {/* Tabs */}
      <div
        role="tablist"
        aria-label="Forge sections"
        className="mb-6 flex flex-wrap gap-1 border-b border-border dark:border-neutral-800"
      >
        {TABS.map((t) => {
          const active = t.id === tab;
          return (
            <button
              key={t.id}
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={active}
              aria-controls={`panel-${t.id}`}
              tabIndex={active ? 0 : -1}
              onClick={() => setTab(t.id)}
              className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                active
                  ? "border-accent text-ink dark:text-neutral-100"
                  : "border-transparent text-dim hover:text-ink dark:text-neutral-400 dark:hover:text-neutral-100"
              }`}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      <p className="mb-6 text-sm text-dim dark:text-neutral-400">
        {TABS.find((t) => t.id === tab)?.blurb}
      </p>

      <section
        role="tabpanel"
        id={`panel-${tab}`}
        aria-labelledby={`tab-${tab}`}
        tabIndex={0}
        className="focus:outline-none"
      >
        {tab === "datasets" && <DatasetsPanel />}
        {tab === "jobs" && <JobsPanel />}
        {tab === "adapters" && <AdaptersPanel />}
        {tab === "evaluation" && <EvalPanel />}
      </section>
    </main>
  );
}
