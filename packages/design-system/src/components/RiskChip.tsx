import * as React from "react";
import { cn } from "../cn";
import type { RiskTone } from "../tokens";

/**
 * Safety/risk tone for world actions and tools. Ronin's safety posture is
 * visible, not buried: every consequential surface can carry one of these.
 */
const RISK: Record<RiskTone, { label: string; cls: string }> = {
  safe: { label: "Safe", cls: "bg-success-soft text-success border-success/30" },
  review: { label: "Review", cls: "bg-info-soft text-info border-info/30" },
  caution: { label: "Caution", cls: "bg-warn-soft text-warn border-warn/30" },
  restricted: { label: "Restricted", cls: "bg-danger-soft text-danger border-danger/30" },
};

export interface RiskChipProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone: RiskTone;
  label?: string;
}

export function RiskChip({ tone, label, className, ...rest }: RiskChipProps) {
  const r = RISK[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[0.6875rem] font-semibold uppercase tracking-wide",
        r.cls,
        className,
      )}
      {...rest}
    >
      {label ?? r.label}
    </span>
  );
}
