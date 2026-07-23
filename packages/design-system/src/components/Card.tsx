import * as React from "react";
import { cn } from "../cn";

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  as?: React.ElementType;
  interactive?: boolean;
}

/** A bordered content container. `interactive` adds hover/press affordance. */
export const Card = React.forwardRef<HTMLDivElement, CardProps>(function Card(
  { as: Tag = "div", interactive, className, ...rest },
  ref,
) {
  return (
    <Tag
      ref={ref}
      className={cn(
        "rounded-xl border border-border bg-surface p-5 text-text",
        interactive &&
          "cursor-pointer transition-[border-color,box-shadow,transform] duration-[220ms] ease-[cubic-bezier(0.2,0.7,0.2,1)] hover:-translate-y-0.5 hover:border-border-strong hover:shadow-[0_4px_16px_rgba(26,24,22,0.08)] focus-within:border-accent",
        className,
      )}
      {...rest}
    />
  );
});
