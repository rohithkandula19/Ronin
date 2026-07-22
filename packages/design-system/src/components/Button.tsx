import * as React from "react";
import { cn } from "../cn";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

const VARIANT: Record<Variant, string> = {
  primary:
    "bg-accent text-on-accent hover:bg-accent-deep active:bg-accent-deep",
  secondary:
    "bg-surface border border-border text-text hover:bg-surface-sunken hover:border-border-strong",
  ghost: "bg-transparent text-text-dim hover:bg-accent/10 hover:text-text",
  danger: "bg-danger text-white hover:brightness-95",
};

const SIZE: Record<Size, string> = {
  sm: "h-8 px-3 text-[0.8125rem] gap-1.5",
  md: "h-10 px-4 text-[0.9375rem] gap-2",
  lg: "h-12 px-6 text-[1.0625rem] gap-2.5",
};

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

/** The RDS action primitive. Calm by default; a single clay accent for intent. */
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    { variant = "secondary", size = "md", loading, className, children, disabled, ...rest },
    ref,
  ) {
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        className={cn(
          "inline-flex select-none items-center justify-center rounded-md font-medium",
          "transition-[background-color,border-color,color,transform] duration-[140ms] ease-[cubic-bezier(0.2,0.7,0.2,1)]",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
          "active:translate-y-px disabled:pointer-events-none disabled:opacity-50",
          VARIANT[variant],
          SIZE[size],
          className,
        )}
        {...rest}
      >
        {loading ? <span className="mr-2 inline-block size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" /> : null}
        {children}
      </button>
    );
  },
);
