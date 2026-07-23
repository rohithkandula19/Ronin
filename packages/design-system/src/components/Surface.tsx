import * as React from "react";
import { cn } from "../cn";

type Elevation = "e0" | "e1" | "e2" | "e3";
const SHADOW: Record<Elevation, string> = {
  e0: "",
  e1: "shadow-[0_1px_2px_rgba(26,24,22,0.06)]",
  e2: "shadow-[0_4px_16px_rgba(26,24,22,0.08)]",
  e3: "shadow-[0_12px_40px_rgba(26,24,22,0.12)]",
};

export interface SurfaceProps extends React.HTMLAttributes<HTMLDivElement> {
  as?: React.ElementType;
  elevation?: Elevation;
  bordered?: boolean;
}

/** A themed background plane. The substrate every other component sits on. */
export const Surface = React.forwardRef<HTMLDivElement, SurfaceProps>(
  function Surface(
    { as: Tag = "div", elevation = "e0", bordered = true, className, ...rest },
    ref,
  ) {
    return (
      <Tag
        ref={ref}
        className={cn(
          "bg-surface text-text",
          bordered && "border border-border",
          SHADOW[elevation],
          className,
        )}
        {...rest}
      />
    );
  },
);
