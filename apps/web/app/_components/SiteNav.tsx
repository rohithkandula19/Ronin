"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Icon, type Theme } from "@ronin/design-system";

const NAV = [
  { href: "/worlds", label: "Worlds" },
  { href: "/coding", label: "Coding" },
  { href: "/education", label: "Education" },
  { href: "/healthcare", label: "Healthcare" },
  { href: "/forge", label: "Forge" },
  { href: "/research", label: "Research" },
  { href: "/vault", label: "Vault" },
  { href: "/tasks", label: "Tasks" },
  { href: "/artifacts", label: "Artifacts" },
];

const THEME_ORDER: Theme[] = ["light", "dark", "oled", "hc"];
const THEME_META: Record<Theme, { icon: Parameters<typeof Icon>[0]["name"]; label: string }> = {
  light: { icon: "sun", label: "Light" },
  dark: { icon: "moon", label: "Sumi (dark)" },
  oled: { icon: "moon", label: "OLED black" },
  hc: { icon: "contrast", label: "High contrast" },
};

function useSharedTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>("light");
  useEffect(() => {
    try {
      const stored = localStorage.getItem("rds-theme") as Theme | null;
      if (stored && THEME_ORDER.includes(stored)) setTheme(stored);
    } catch {
      /* ignore */
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

/**
 * The marketing/site nav. Hidden on the OS shell (`/os/*`), which brings its
 * own chrome. Carries a theme toggle so the whole site shares one theme.
 */
export function SiteNav() {
  const pathname = usePathname();
  const [theme, cycleTheme] = useSharedTheme();
  if (pathname === "/os" || pathname?.startsWith("/os/")) return null;

  return (
    <nav
      aria-label="Ronin AI OS"
      className="sticky top-0 z-40 flex items-center gap-1 overflow-x-auto border-b border-border bg-bg/90 px-4 py-2 text-sm backdrop-blur"
    >
      <Link href="/" className="mr-2 shrink-0 font-semibold text-ink">
        Ronin<span className="text-accent"> AI OS</span>
      </Link>
      {NAV.map((n) => (
        <Link
          key={n.href}
          href={n.href}
          className="shrink-0 rounded-md px-3 py-1.5 text-dim transition hover:bg-accent/10 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {n.label}
        </Link>
      ))}
      <a
        href="https://github.com/rohithkandula19/Ronin"
        target="_blank"
        rel="noreferrer"
        className="ml-auto shrink-0 rounded-md px-3 py-1.5 font-medium text-dim transition hover:bg-accent/10 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        GitHub ↗
      </a>
      <button
        onClick={cycleTheme}
        aria-label={`Theme: ${THEME_META[theme].label} (click to change)`}
        title={`Theme: ${THEME_META[theme].label}`}
        className="ml-auto grid size-8 shrink-0 place-items-center rounded-md text-dim transition hover:bg-accent/10 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <Icon name={THEME_META[theme].icon} size={17} />
      </button>
    </nav>
  );
}
