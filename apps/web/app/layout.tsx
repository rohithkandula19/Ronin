import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Ronin AI OS — specialized intelligence, one world at a time",
  description:
    "A local-first, provider-agnostic AI operating system. Enter an industry world — Coding, Education, Healthcare Information — with its own tools, safety rules, memory boundaries, and evaluations.",
};

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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <nav
          aria-label="Ronin AI OS"
          className="sticky top-0 z-40 flex items-center gap-1 overflow-x-auto border-b border-border bg-bg/90 px-4 py-2 text-sm backdrop-blur"
        >
          <a href="/" className="mr-2 shrink-0 font-semibold text-ink">
            Ronin<span className="text-accent"> AI OS</span>
          </a>
          {NAV.map((n) => (
            <a
              key={n.href}
              href={n.href}
              className="shrink-0 rounded-md px-3 py-1.5 text-dim transition hover:bg-accent/10 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              {n.label}
            </a>
          ))}
        </nav>
        {children}
      </body>
    </html>
  );
}
