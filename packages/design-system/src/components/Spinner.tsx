import * as React from "react";
import { cn } from "../cn";

/** A minimal spinner. Respects reduced-motion via the .rds-spin animation. */
export function Spinner({
  className,
  label = "Loading",
  ...rest
}: React.HTMLAttributes<HTMLSpanElement> & { label?: string }) {
  return (
    <span
      role="status"
      aria-label={label}
      className={cn(
        "inline-block size-4 animate-spin rounded-full border-2 border-current border-t-transparent opacity-70",
        className,
      )}
      {...rest}
    />
  );
}
