"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon, cn, type IconName } from "@ronin/design-system";
import { WORLDS, GLOBAL_DESTINATIONS } from "../_data";

const AVAILABLE_WORLDS = WORLDS.filter((w) => w.status === "available");

function RailItem({
  icon,
  label,
  href,
  expanded,
  active,
  onNavigate,
}: {
  icon: IconName;
  label: string;
  href: string;
  expanded: boolean;
  active?: boolean;
  onNavigate?: () => void;
}) {
  return (
    <Link
      href={href}
      onClick={onNavigate}
      title={expanded ? undefined : label}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group relative flex h-10 items-center rounded-lg text-text-dim transition-colors duration-[140ms]",
        expanded ? "gap-3 px-3" : "justify-center",
        active ? "bg-accent/12 text-text" : "hover:bg-accent/8 hover:text-text",
      )}
    >
      {active && (
        <span className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-accent" aria-hidden />
      )}
      <Icon name={icon} size={20} />
      {expanded && <span className="truncate text-[0.875rem]">{label}</span>}
    </Link>
  );
}

function SectionLabel({ children, expanded }: { children: React.ReactNode; expanded: boolean }) {
  if (!expanded) return <div className="mx-auto my-2 h-px w-6 bg-border" aria-hidden />;
  return (
    <div className="px-3 pb-1 pt-4 text-[0.6875rem] font-semibold uppercase tracking-wider text-text-faint">
      {children}
    </div>
  );
}

export function LeftRail({
  expanded,
  onToggle,
  onOpenCommand,
}: {
  expanded: boolean;
  onToggle: () => void;
  onOpenCommand: () => void;
}) {
  const pathname = usePathname();
  const isHome = pathname === "/os";

  return (
    <nav
      aria-label="Ronin"
      className={cn(
        "flex h-full shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-[220ms] ease-standard",
        expanded ? "w-[260px]" : "w-[72px]",
      )}
    >
      {/* Brand + collapse toggle */}
      <div className={cn("flex h-16 items-center", expanded ? "px-4" : "justify-center")}>
        <Link href="/os" className="flex items-center gap-2.5" aria-label="Ronin home">
          <span className="grid size-9 place-items-center rounded-xl bg-ink text-bg" aria-hidden>
            <span className="text-[1.05rem] font-semibold">円</span>
          </span>
          {expanded && (
            <span className="text-[1.05rem] font-semibold tracking-tight text-text">
              Ronin
            </span>
          )}
        </Link>
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-3">
        <RailItem icon="home" label="Home" href="/os" expanded={expanded} active={isHome} />
        <button
          onClick={onOpenCommand}
          className={cn(
            "mt-1 flex h-10 w-full items-center rounded-lg text-text-dim transition-colors hover:bg-accent/8 hover:text-text",
            expanded ? "gap-3 px-3" : "justify-center",
          )}
          title="Command Center (⌘K)"
        >
          <Icon name="command" size={20} />
          {expanded && (
            <>
              <span className="text-[0.875rem]">Command</span>
              <kbd className="ml-auto rounded border border-border bg-surface-sunken px-1.5 py-0.5 font-mono text-[0.625rem] text-text-faint">
                ⌘K
              </kbd>
            </>
          )}
        </button>

        <SectionLabel expanded={expanded}>Worlds</SectionLabel>
        {AVAILABLE_WORLDS.map((w) => (
          <RailItem
            key={w.id}
            icon={w.icon}
            label={w.name}
            href={w.href ?? "/worlds"}
            expanded={expanded}
            active={pathname === w.href}
          />
        ))}
        <RailItem icon="worlds" label="All worlds" href="/worlds" expanded={expanded} active={pathname === "/worlds"} />

        <SectionLabel expanded={expanded}>Across worlds</SectionLabel>
        {GLOBAL_DESTINATIONS.map((d) => (
          <RailItem
            key={d.id}
            icon={d.icon}
            label={d.label}
            href={d.href}
            expanded={expanded}
            active={pathname === d.href && !isHome}
          />
        ))}
      </div>

      {/* Footer: Forge + collapse */}
      <div className="border-t border-border px-3 py-3">
        <RailItem icon="forge" label="Forge" href="/forge" expanded={expanded} active={pathname === "/forge"} />
        <button
          onClick={onToggle}
          className={cn(
            "mt-1 flex h-9 w-full items-center rounded-lg text-text-faint transition-colors hover:bg-accent/8 hover:text-text",
            expanded ? "gap-3 px-3" : "justify-center",
          )}
          aria-label={expanded ? "Collapse navigation" : "Expand navigation"}
        >
          <Icon name={expanded ? "chevronLeft" : "chevronRight"} size={18} />
          {expanded && <span className="text-[0.8125rem]">Collapse</span>}
        </button>
      </div>
    </nav>
  );
}
