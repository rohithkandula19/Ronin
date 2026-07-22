import * as React from "react";
import { cn } from "../cn";

type Size = "sm" | "md" | "lg";
const SIZE: Record<Size, string> = {
  sm: "size-7 text-[0.6875rem]",
  md: "size-9 text-[0.8125rem]",
  lg: "size-12 text-[1rem]",
};

export interface AvatarProps extends React.HTMLAttributes<HTMLSpanElement> {
  name: string;
  size?: Size;
}

/** Initials avatar in the accent tint. No image fetching (local-first). */
export function Avatar({ name, size = "md", className, ...rest }: AvatarProps) {
  const initials = name
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex items-center justify-center rounded-full bg-accent-tint font-semibold text-accent-deep",
        SIZE[size],
        className,
      )}
      title={name}
      {...rest}
    >
      {initials || "·"}
    </span>
  );
}
