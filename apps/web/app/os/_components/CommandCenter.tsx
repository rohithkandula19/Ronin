"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Icon, Kbd, cn, type IconName } from "@ronin/design-system";
import { WORLDS, GLOBAL_DESTINATIONS, SUGGESTED_ACTIONS } from "../_data";

interface Command {
  id: string;
  label: string;
  hint?: string;
  icon: IconName;
  group: "Go to" | "Do" | "Ask Ronin" | "Run agent";
  run: (router: ReturnType<typeof useRouter>) => void;
}

function buildCommands(query: string): Command[] {
  const cmds: Command[] = [];

  cmds.push({
    id: "go-home",
    label: "Home",
    icon: "home",
    group: "Go to",
    run: (r) => r.push("/os"),
  });
  for (const w of WORLDS.filter((w) => w.status === "available")) {
    cmds.push({
      id: `go-${w.id}`,
      label: w.name,
      hint: "world",
      icon: w.icon,
      group: "Go to",
      run: (r) => r.push(w.href ?? "/worlds"),
    });
  }
  for (const d of GLOBAL_DESTINATIONS) {
    cmds.push({ id: `go-${d.id}`, label: d.label, icon: d.icon, group: "Go to", run: (r) => r.push(d.href) });
  }

  for (const s of SUGGESTED_ACTIONS) {
    cmds.push({ id: `do-${s.id}`, label: s.label, hint: s.detail, icon: s.icon, group: "Do", run: () => {} });
  }

  const q = query.trim();
  if (q) {
    cmds.unshift({
      id: "ask",
      label: `Ask Ronin: "${q}"`,
      hint: "opens in the current world",
      icon: "spark",
      group: "Ask Ronin",
      run: () => {},
    });
    cmds.push({
      id: "agent",
      label: `Run an agent on "${q}"`,
      hint: "with an approval gate",
      icon: "agents",
      group: "Run agent",
      run: () => {},
    });
  }
  return cmds;
}

export function CommandCenter({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const results = useMemo(() => {
    const all = buildCommands(query);
    const q = query.trim().toLowerCase();
    if (!q) return all;
    return all.filter(
      (c) => c.group === "Ask Ronin" || c.group === "Run agent" || c.label.toLowerCase().includes(q),
    );
  }, [query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      const t = setTimeout(() => inputRef.current?.focus(), 20);
      return () => clearTimeout(t);
    }
  }, [open]);

  useEffect(() => setActive(0), [query]);

  if (!open) return null;

  const grouped = results.reduce<Record<string, Command[]>>((acc, c) => {
    (acc[c.group] ||= []).push(c);
    return acc;
  }, {});
  const flat = results;

  function choose(cmd: Command | undefined) {
    if (!cmd) return;
    cmd.run(router);
    onClose();
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, flat.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(flat[active]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  }

  let runningIndex = -1;

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center bg-ink/30 px-4 pt-[12vh] backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Command Center"
    >
      <div
        className="fadeup w-full max-w-xl overflow-hidden rounded-2xl border border-border bg-surface shadow-[0_12px_40px_rgba(26,24,22,0.12)]"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="flex items-center gap-3 border-b border-border px-4">
          <Icon name="search" size={18} className="text-text-dim" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Navigate, act, ask, or run an agent…"
            className="h-14 flex-1 bg-transparent text-[0.9375rem] text-text placeholder:text-text-faint focus:outline-none"
          />
          <Kbd>Esc</Kbd>
        </div>

        <div className="max-h-[52vh] overflow-y-auto p-2">
          {flat.length === 0 && (
            <p className="px-3 py-6 text-center text-[0.8125rem] text-text-dim">No matches.</p>
          )}
          {Object.entries(grouped).map(([group, cmds]) => (
            <div key={group} className="mb-1">
              <div className="px-3 py-1.5 text-[0.6875rem] font-semibold uppercase tracking-wider text-text-faint">
                {group}
              </div>
              {cmds.map((c) => {
                runningIndex += 1;
                const idx = runningIndex;
                return (
                  <button
                    key={c.id}
                    onMouseEnter={() => setActive(idx)}
                    onClick={() => choose(c)}
                    className={cn(
                      "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors",
                      idx === active ? "bg-accent/12" : "hover:bg-accent/8",
                    )}
                  >
                    <Icon name={c.icon} size={18} className="shrink-0 text-text-dim" />
                    <span className="flex-1 truncate text-[0.875rem] text-text">{c.label}</span>
                    {c.hint && <span className="truncate text-[0.75rem] text-text-faint">{c.hint}</span>}
                    {idx === active && <Icon name="arrowRight" size={16} className="text-accent" />}
                  </button>
                );
              })}
            </div>
          ))}
        </div>

        <div className="flex items-center justify-between border-t border-border px-4 py-2 text-[0.6875rem] text-text-faint">
          <span className="flex items-center gap-1.5">
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd>
            to navigate · <Kbd>↵</Kbd> to select
          </span>
          <span>Ronin Command Center</span>
        </div>
      </div>
    </div>
  );
}
