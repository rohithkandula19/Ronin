import * as React from "react";
import { cn } from "../cn";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
}

/** The RDS text input. Monospace-neutral, calm focus ring. */
export const Input = React.forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid, className, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(
        "h-10 w-full rounded-md border bg-surface px-3 text-[0.9375rem] text-text placeholder:text-text-faint",
        "transition-colors duration-[140ms] focus:outline-none focus:ring-2 focus:ring-accent",
        invalid ? "border-danger" : "border-border",
        className,
      )}
      {...rest}
    />
  );
});

export interface FieldProps {
  label: string;
  htmlFor: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
  className?: string;
}

/** Label + control + hint/error, wired for accessibility. */
export function Field({ label, htmlFor, hint, error, children, className }: FieldProps) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={htmlFor} className="text-[0.8125rem] font-medium text-text">
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-[0.75rem] text-danger">{error}</p>
      ) : hint ? (
        <p className="text-[0.75rem] text-text-dim">{hint}</p>
      ) : null}
    </div>
  );
}
