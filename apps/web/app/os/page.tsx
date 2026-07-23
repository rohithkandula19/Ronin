"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Icon, RiskChip, Badge, StatusLabel, cn } from "@ronin/design-system";
import {
  WORLDS,
  SUGGESTED_ACTIONS,
  RECENT_ARTIFACTS,
  greeting,
} from "./_data";

function openCommand() {
  window.dispatchEvent(new CustomEvent("ronin:open-command"));
}

export default function RoninHome() {
  // Greeting + date are derived on the client so they match the viewer's clock.
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => setNow(new Date()), []);
  const hello = now ? greeting(now.getHours()) : "Welcome";
  const dateLine = now
    ? now.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })
    : "";

  const available = WORLDS.filter((w) => w.status === "available");
  const preparing = WORLDS.filter((w) => w.status === "preparing");

  return (
    <div className="mx-auto min-h-full max-w-app px-6 py-10 sm:px-10 lg:px-14">
      {/* ── Greeting: a place, not a prompt box ── */}
      <header className="fadeup mb-10">
        <div className="mb-3 flex items-center gap-2 text-[0.75rem] font-medium uppercase tracking-wider text-text-faint">
          <span className="grid size-5 place-items-center rounded-full bg-ink text-[0.625rem] text-bg">円</span>
          {dateLine || "Ronin OS"}
        </div>
        <h1 className="font-display text-[2.75rem] leading-[1.05] text-text sm:text-[3.25rem]">
          {hello}.
        </h1>
        <p className="mt-3 max-w-xl text-[1.0625rem] leading-relaxed text-text-dim">
          Pick up where you left off, or step into a world. Ronin keeps each
          domain — its tools, memory, and safety — in its own place.
        </p>

        <div className="mt-6 flex flex-wrap items-center gap-3">
          <button
            onClick={openCommand}
            className="inline-flex items-center gap-2.5 rounded-xl bg-accent px-5 py-3 text-[0.9375rem] font-medium text-on-accent transition-colors hover:bg-accent-deep"
          >
            <Icon name="command" size={18} />
            Open Command Center
            <kbd className="ml-1 rounded border border-white/25 px-1.5 py-0.5 font-mono text-[0.6875rem]">⌘K</kbd>
          </button>
          <Link
            href="/worlds"
            className="inline-flex items-center gap-2 rounded-xl border border-border bg-surface px-5 py-3 text-[0.9375rem] font-medium text-text transition-colors hover:bg-surface-sunken"
          >
            Browse all worlds
            <Icon name="arrowRight" size={16} />
          </Link>
        </div>
      </header>

      {/* ── Continue ── */}
      <Section title="Continue" hint="Waiting on you or freshly queued">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {SUGGESTED_ACTIONS.map((s) => (
            <button
              key={s.id}
              onClick={openCommand}
              className="group flex items-start gap-3 rounded-xl border border-border bg-surface p-4 text-left transition-all duration-[220ms] ease-standard hover:-translate-y-0.5 hover:border-border-strong hover:shadow-[0_4px_16px_rgba(26,24,22,0.08)]"
            >
              <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-accent-tint text-accent-deep">
                <Icon name={s.icon} size={20} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[0.9375rem] font-medium text-text">{s.label}</span>
                <span className="mt-0.5 block truncate text-[0.8125rem] text-text-dim">{s.detail}</span>
              </span>
              <Icon
                name="arrowRight"
                size={16}
                className="mt-1 shrink-0 text-text-faint transition-transform group-hover:translate-x-0.5 group-hover:text-accent"
              />
            </button>
          ))}
        </div>
      </Section>

      {/* ── Worlds ── */}
      <Section title="Your worlds" hint={`${available.length} available · ${preparing.length} in preparation`}>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {available.map((w) => (
            <Link
              key={w.id}
              href={w.href ?? "/worlds"}
              className="card-lift hairline group flex flex-col rounded-xl border border-border bg-surface p-5"
            >
              <div className="mb-3 flex items-center justify-between">
                <span className="grid size-11 place-items-center rounded-xl bg-surface-sunken text-text group-hover:bg-accent-tint group-hover:text-accent-deep">
                  <Icon name={w.icon} size={22} />
                </span>
                <RiskChip tone={w.risk} />
              </div>
              <h3 className="text-[1.0625rem] font-semibold text-text">{w.name}</h3>
              <p className="mt-1 flex-1 text-[0.8125rem] leading-relaxed text-text-dim">{w.tagline}</p>
              {w.note && (
                <p className="mt-2 text-[0.75rem] font-medium text-warn">{w.note}</p>
              )}
              <span className="mt-4 inline-flex items-center gap-1.5 text-[0.8125rem] font-medium text-accent">
                Enter
                <Icon name="arrowRight" size={14} className="transition-transform group-hover:translate-x-0.5" />
              </span>
            </Link>
          ))}
        </div>

        <div className="mt-4 rounded-xl border border-dashed border-border bg-surface-sunken/50 p-5">
          <div className="mb-3 flex items-center gap-2">
            <Icon name="clock" size={16} className="text-text-dim" />
            <span className="text-[0.8125rem] font-medium text-text-dim">In preparation</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {preparing.map((w) => (
              <span
                key={w.id}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-1.5 text-[0.8125rem] text-text-dim"
              >
                <Icon name={w.icon} size={15} />
                {w.name}
              </span>
            ))}
          </div>
        </div>
      </Section>

      {/* ── Recent artifacts ── */}
      <Section title="Recent artifacts" hint="Structured, versioned, provenance-tracked">
        <div className="overflow-hidden rounded-xl border border-border bg-surface">
          {RECENT_ARTIFACTS.map((a, i) => (
            <Link
              key={a.id}
              href="/artifacts"
              className={cn(
                "flex items-center gap-4 px-4 py-3.5 transition-colors hover:bg-surface-sunken",
                i > 0 && "border-t border-border",
              )}
            >
              <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-surface-sunken text-text-dim">
                <Icon name="artifacts" size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[0.9375rem] font-medium text-text">{a.title}</span>
                <span className="text-[0.75rem] text-text-faint">
                  {a.kind} · {a.world}
                </span>
              </span>
              <span className="shrink-0 text-[0.75rem] text-text-faint">{a.when}</span>
              <Icon name="chevronRight" size={16} className="shrink-0 text-text-faint" />
            </Link>
          ))}
        </div>
      </Section>

      <footer className="mt-12 flex flex-wrap items-center gap-3 border-t border-border pt-6 text-[0.8125rem] text-text-dim">
        <StatusLabel kind="IMPLEMENTED" detail="offline preview" />
        <span>
          Running without a backend. Content shown is a labelled sample; connect an
          API to see live worlds, memory, and agents.
        </span>
        <Link href="/" className="ml-auto inline-flex items-center gap-1 font-medium text-accent hover:underline">
          About Ronin
          <Icon name="arrowRight" size={14} />
        </Link>
      </footer>
    </div>
  );
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="fadeup mb-10">
      <div className="mb-4 flex items-baseline justify-between gap-4">
        <h2 className="text-[1.25rem] font-semibold tracking-tight text-text">{title}</h2>
        {hint && <span className="text-[0.8125rem] text-text-faint">{hint}</span>}
      </div>
      {children}
    </section>
  );
}
