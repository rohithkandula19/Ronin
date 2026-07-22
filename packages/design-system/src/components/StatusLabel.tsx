import * as React from "react";
import { cn } from "../cn";
import type { StatusKind } from "../tokens";

/**
 * The honest-status primitive. Ronin never invents a status outside this set;
 * this component is the ONLY sanctioned way to render one, so the vocabulary
 * stays consistent and truthful across the product.
 */
const STATUS: Record<
  StatusKind,
  { label: string; dot: string; text: string; ring: string }
> = {
  VERIFIED: { label: "Verified", dot: "bg-success", text: "text-success", ring: "border-success/30 bg-success-soft" },
  IMPLEMENTED: { label: "Implemented", dot: "bg-info", text: "text-info", ring: "border-info/30 bg-info-soft" },
  DRAFT: { label: "Draft", dot: "bg-warn", text: "text-warn", ring: "border-warn/30 bg-warn-soft" },
  BLOCKED: { label: "Blocked", dot: "bg-danger", text: "text-danger", ring: "border-danger/30 bg-danger-soft" },
  DISABLED: { label: "Disabled", dot: "bg-ink-300", text: "text-text-faint", ring: "border-border bg-surface-sunken" },
};

export interface StatusLabelProps extends React.HTMLAttributes<HTMLSpanElement> {
  kind: StatusKind;
  /** Optional detail appended after the base label, e.g. "BLOCKED — no GPU". */
  detail?: string;
}

export function StatusLabel({ kind, detail, className, ...rest }: StatusLabelProps) {
  const s = STATUS[kind];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[0.75rem] font-medium",
        s.ring,
        s.text,
        className,
      )}
      {...rest}
    >
      <span className={cn("size-1.5 rounded-full", s.dot)} aria-hidden />
      {s.label}
      {detail ? <span className="opacity-70">— {detail}</span> : null}
    </span>
  );
}
