"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

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

/**
 * The marketing/site nav. Hidden on the OS shell (`/os/*`), which brings its
 * own chrome (left rail + workspace header). This keeps the immersive app
 * experience free of a redundant top bar.
 */
export function SiteNav() {
  const pathname = usePathname();
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
    </nav>
  );
}
