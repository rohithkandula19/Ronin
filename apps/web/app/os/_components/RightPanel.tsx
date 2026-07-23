"use client";

import { useState } from "react";
import { Icon, StatusLabel, RiskChip, Badge, cn } from "@ronin/design-system";
import { SAMPLE_PLAN, MEMORY_ITEMS } from "../_data";

type Tab = "context" | "memory" | "plan" | "approvals";
const TABS: { id: Tab; label: string; icon: Parameters<typeof Icon>[0]["name"] }[] = [
  { id: "context", label: "Context", icon: "knowledge" },
  { id: "memory", label: "Memory", icon: "memory" },
  { id: "plan", label: "Plan", icon: "tasks" },
  { id: "approvals", label: "Approvals", icon: "shield" },
];

const STEP_ICON = {
  done: { name: "check", cls: "text-success" },
  active: { name: "play", cls: "text-accent" },
  approval: { name: "alert", cls: "text-warn" },
  todo: { name: "dot", cls: "text-text-faint" },
} as const;

export function RightPanel({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("plan");
  const approvals = SAMPLE_PLAN.steps.filter((s) => s.state === "approval").length;

  return (
    <aside
      aria-label="Intelligence panel"
      className="flex h-full w-[360px] shrink-0 flex-col border-l border-border bg-surface"
    >
      <div className="flex h-16 items-center justify-between px-4">
        <span className="text-[0.9375rem] font-semibold text-text">Intelligence</span>
        <button
          onClick={onClose}
          aria-label="Close intelligence panel"
          className="grid size-8 place-items-center rounded-md text-text-dim hover:bg-accent/10 hover:text-text"
        >
          <Icon name="panelRight" size={18} />
        </button>
      </div>

      <div role="tablist" className="flex gap-1 border-b border-border px-3 pb-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "relative flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[0.75rem] font-medium transition-colors",
              tab === t.id ? "bg-accent/12 text-text" : "text-text-dim hover:text-text",
            )}
          >
            <Icon name={t.icon} size={14} />
            {t.label}
            {t.id === "approvals" && approvals > 0 && (
              <span className="ml-0.5 grid size-4 place-items-center rounded-full bg-warn text-[0.625rem] font-bold text-white">
                {approvals}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {tab === "context" && (
          <div className="space-y-4">
            <PanelNote>What Ronin is drawing on right now, for this world.</PanelNote>
            <div className="space-y-2">
              <Row label="World" value="Coding" />
              <Row label="Model" value="claude-sonnet · provider-agnostic" />
              <Row label="Knowledge" value="3 local sources indexed" />
              <Row label="Autonomy" value="Ask before acting" />
            </div>
          </div>
        )}

        {tab === "memory" && (
          <div className="space-y-3">
            <PanelNote>Memory you can see and revoke. Nothing is hidden.</PanelNote>
            {MEMORY_ITEMS.map((m) => (
              <div key={m.id} className="rounded-lg border border-border bg-surface-sunken p-3">
                <div className="mb-1 flex items-center justify-between">
                  <Badge tone="accent">{m.scope}</Badge>
                  <button className="text-[0.6875rem] font-medium text-text-dim hover:text-danger">
                    Forget
                  </button>
                </div>
                <p className="text-[0.8125rem] text-text">{m.text}</p>
              </div>
            ))}
          </div>
        )}

        {tab === "plan" && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-[0.8125rem] font-semibold text-text">{SAMPLE_PLAN.title}</span>
              <Badge tone="neutral">{SAMPLE_PLAN.world}</Badge>
            </div>
            <ol className="space-y-0.5">
              {SAMPLE_PLAN.steps.map((s, i) => {
                const ic = STEP_ICON[s.state];
                return (
                  <li key={i} className="flex items-start gap-2.5 rounded-md px-1 py-1.5">
                    <span className={cn("mt-0.5 shrink-0", ic.cls)}>
                      <Icon name={ic.name} size={16} />
                    </span>
                    <span className={cn("text-[0.8125rem]", s.state === "todo" ? "text-text-dim" : "text-text")}>
                      {s.label}
                      {s.state === "approval" && (
                        <span className="mt-1 block">
                          <RiskChip tone="caution" label="Awaiting your approval" />
                        </span>
                      )}
                    </span>
                  </li>
                );
              })}
            </ol>
          </div>
        )}

        {tab === "approvals" && (
          <div className="space-y-3">
            <PanelNote>Ronin asks before it acts. Consequential steps stop here.</PanelNote>
            <div className="rounded-lg border border-warn/40 bg-warn-soft p-4">
              <div className="mb-2 flex items-center gap-2">
                <Icon name="alert" size={16} className="text-warn" />
                <span className="text-[0.8125rem] font-semibold text-text">Apply diff to working tree</span>
              </div>
              <p className="mb-3 text-[0.8125rem] text-text-dim">
                4 files · +38 −12. Runs on your machine. Reversible via git.
              </p>
              <div className="flex gap-2">
                <button className="flex-1 rounded-md bg-accent px-3 py-1.5 text-[0.8125rem] font-medium text-on-accent hover:bg-accent-deep">
                  Approve
                </button>
                <button className="flex-1 rounded-md border border-border bg-surface px-3 py-1.5 text-[0.8125rem] font-medium text-text hover:bg-surface-sunken">
                  Review diff
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="border-t border-border px-4 py-3">
        <StatusLabel kind="IMPLEMENTED" detail="offline sample" />
      </div>
    </aside>
  );
}

function PanelNote({ children }: { children: React.ReactNode }) {
  return <p className="text-[0.75rem] leading-relaxed text-text-dim">{children}</p>;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-border py-2 last:border-0">
      <span className="text-[0.75rem] text-text-dim">{label}</span>
      <span className="text-right text-[0.8125rem] text-text">{value}</span>
    </div>
  );
}
