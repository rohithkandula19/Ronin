"use client";

import { cn } from "@ronin/design-system";
import type { LiveStatus } from "../_worldData";

const MAP: Record<LiveStatus, { dot: string; label: string }> = {
  loading: { dot: "bg-text-faint animate-pulse", label: "Connecting…" },
  connected: { dot: "bg-success", label: "Live · API" },
  offline: { dot: "bg-text-faint", label: "Offline · sample" },
};

/** Shared live/offline indicator for world surfaces. */
export function ConnectionBadge({ status, error }: { status: LiveStatus; error?: string }) {
  const m = MAP[status];
  return (
    <span
      title={
        status === "offline"
          ? `/api/v1 unreachable${error ? ` (${error})` : ""} — showing sample`
          : status === "connected"
            ? "Connected to /api/v1"
            : "Checking /api/v1…"
      }
      className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 py-0.5 text-[0.75rem] font-medium text-text-dim"
    >
      <span className={cn("size-1.5 rounded-full", m.dot)} />
      {m.label}
    </span>
  );
}
