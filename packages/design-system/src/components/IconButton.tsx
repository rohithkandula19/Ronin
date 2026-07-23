import * as React from "react";
import { cn } from "../cn";

type Size = "sm" | "md" | "lg";
const SIZE: Record<Size, string> = { sm: "size-8", md: "size-10", lg: "size-12" };

export interface IconButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required: icon buttons have no visible text, so they must be labelled. */
  label: string;
  size?: Size;
  active?: boolean;
}

/** Square, icon-only action. Enforces an accessible name via `label`. */
export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton({ label, size = "md", active, className, children, ...rest }, ref) {
    return (
      <button
        ref={ref}
        aria-label={label}
        title={label}
        aria-pressed={active ?? undefined}
        className={cn(
          "inline-flex items-center justify-center rounded-md text-text-dim",
          "transition-colors duration-[140ms] hover:bg-accent/10 hover:text-text",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
          active && "bg-accent/12 text-text",
          SIZE[size],
          className,
        )}
        {...rest}
      >
        {children}
      </button>
    );
  },
);
