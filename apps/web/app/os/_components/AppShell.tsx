"use client";

import { useCallback, useEffect, useState } from "react";
import { Icon, IconButton, type Theme } from "@ronin/design-system";
import { LeftRail } from "./LeftRail";
import { RightPanel } from "./RightPanel";
import { CommandCenter } from "./CommandCenter";

const THEME_ORDER: Theme[] = ["light", "dark", "oled", "hc"];
const THEME_META: Record<Theme, { icon: Parameters<typeof Icon>[0]["name"]; label: string }> = {
  light: { icon: "sun", label: "Light" },
  dark: { icon: "moon", label: "Sumi (dark)" },
  oled: { icon: "moon", label: "OLED black" },
  hc: { icon: "contrast", label: "High contrast" },
};

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>("light");

  useEffect(() => {
    const stored = (typeof localStorage !== "undefined" && localStorage.getItem("rds-theme")) as Theme | null;
    if (stored && THEME_ORDER.includes(stored)) {
      setTheme(stored);
      document.documentElement.setAttribute("data-theme", stored);
    }
  }, []);

  const cycle = useCallback(() => {
    setTheme((cur) => {
      const next = THEME_ORDER[(THEME_ORDER.indexOf(cur) + 1) % THEME_ORDER.length];
      document.documentElement.setAttribute("data-theme", next);
      try {
        localStorage.setItem("rds-theme", next);
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  return [theme, cycle];
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [railExpanded, setRailExpanded] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);
  const [commandOpen, setCommandOpen] = useState(false);
  const [theme, cycleTheme] = useTheme();

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCommandOpen((o) => !o);
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "\\") {
        e.preventDefault();
        setPanelOpen((o) => !o);
      }
    }
    function onOpenCommand() {
      setCommandOpen(true);
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("ronin:open-command", onOpenCommand);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("ronin:open-command", onOpenCommand);
    };
  }, []);

  return (
    <div className="flex h-[100dvh] w-full overflow-hidden bg-bg text-text">
      <LeftRail
        expanded={railExpanded}
        onToggle={() => setRailExpanded((e) => !e)}
        onOpenCommand={() => setCommandOpen(true)}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Workspace header */}
        <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border bg-surface/80 px-5 backdrop-blur">
          <button
            onClick={() => setCommandOpen(true)}
            className="flex h-9 min-w-0 max-w-md flex-1 items-center gap-2.5 rounded-lg border border-border bg-surface-sunken px-3 text-left text-text-dim transition-colors hover:border-border-strong"
          >
            <Icon name="search" size={16} />
            <span className="flex-1 truncate text-[0.8125rem]">Search, act, or ask Ronin…</span>
            <kbd className="rounded border border-border bg-surface px-1.5 py-0.5 font-mono text-[0.625rem]">⌘K</kbd>
          </button>

          <div className="ml-auto flex items-center gap-1">
            <IconButton
              label={`Theme: ${THEME_META[theme].label} (click to change)`}
              onClick={cycleTheme}
              size="md"
            >
              <Icon name={THEME_META[theme].icon} size={18} />
            </IconButton>
            <IconButton
              label={panelOpen ? "Hide intelligence panel" : "Show intelligence panel"}
              onClick={() => setPanelOpen((o) => !o)}
              active={panelOpen}
              size="md"
            >
              <Icon name="panelRight" size={18} />
            </IconButton>
          </div>
        </header>

        <div className="flex min-h-0 flex-1">
          <main className="min-w-0 flex-1 overflow-y-auto">{children}</main>
          {panelOpen && <RightPanel onClose={() => setPanelOpen(false)} />}
        </div>
      </div>

      <CommandCenter open={commandOpen} onClose={() => setCommandOpen(false)} />
    </div>
  );
}
