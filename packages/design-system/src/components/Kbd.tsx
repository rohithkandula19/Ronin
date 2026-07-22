import * as React from "react";
import { cn } from "../cn";

/** A keyboard-key hint. Used throughout the Command Center and shortcuts. */
export function Kbd({ className, children, ...rest }: React.HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        "inline-flex min-w-[1.5rem] items-center justify-center rounded border border-border bg-surface-sunken px-1.5 py-0.5",
        "font-mono text-[0.6875rem] font-medium text-text-dim shadow-[0_1px_0_rgba(26,24,22,0.06)]",
        className,
      )}
      {...rest}
    >
      {children}
    </kbd>
  );
}
