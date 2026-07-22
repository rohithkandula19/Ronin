import * as React from "react";
import { cn } from "../cn";

type Tone = "neutral" | "accent" | "success" | "warn" | "danger" | "info";
const TONE: Record<Tone, string> = {
  neutral: "bg-surface-sunken text-text-dim border-border",
  accent: "bg-accent-tint text-accent-deep border-accent-soft/40",
  success: "bg-success-soft text-success border-success/30",
  warn: "bg-warn-soft text-warn border-warn/30",
  danger: "bg-danger-soft text-danger border-danger/30",
  info: "bg-info-soft text-info border-info/30",
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
}

/** A small, quiet label chip. */
export function Badge({ tone = "neutral", className, ...rest }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[0.75rem] font-medium tracking-[0.01em]",
        TONE[tone],
        className,
      )}
      {...rest}
    />
  );
}
