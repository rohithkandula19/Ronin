import * as React from "react";

/**
 * RDS icon set — a small, curated collection of line icons drawn on a 24×24
 * grid with a 1.75 stroke, `currentColor`, round caps/joins. Line-style only:
 * quiet, precise, and legible at 16–24px. No external icon dependency
 * (Ronin is local-first; nothing is fetched at runtime).
 *
 * To add an icon: add a `<path>`/primitive string to ICONS keyed by name.
 */
const ICONS: Record<string, React.ReactNode> = {
  home: <path d="M4 10.5 12 4l8 6.5M6 9.5V20h12V9.5M10 20v-5h4v5" />,
  worlds: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M3.5 12h17M12 3.5c2.4 2.3 3.6 5.3 3.6 8.5S14.4 18.2 12 20.5c-2.4-2.3-3.6-5.3-3.6-8.5S9.6 5.8 12 3.5Z" />
    </>
  ),
  code: <path d="m8.5 8.5-4 3.5 4 3.5M15.5 8.5l4 3.5-4 3.5M13.5 6l-3 12" />,
  education: <path d="M12 5 3 9l9 4 9-4-9-4ZM6 11v4.5c0 1 2.7 2.5 6 2.5s6-1.5 6-2.5V11M21 9v4.5" />,
  healthcare: <path d="M3.5 12.5h4L9 9l3 7 2-4.5 1.5 1h5" />,
  finance: <path d="M4 19V5M4 19h16M8 15l3-4 3 3 4-6" />,
  business: <path d="M6 20V8l6-3 6 3v12M6 20h12M10 20v-4h4v4M9 11h.01M15 11h.01" />,
  legal: <path d="M12 4v16M6 20h12M12 6 5 8l2.5 5a3 3 0 0 1-5 0L5 8M12 6l7 2-2.5 5a3 3 0 0 0 5 0L19 8" />,
  science: <path d="M9 4h6M10 4v5.5L5.5 17a2 2 0 0 0 1.8 3h9.4a2 2 0 0 0 1.8-3L14 9.5V4M8 14h8" />,
  creative: <path d="M12 3a9 9 0 1 0 0 18c1.4 0 2-1 2-2 0-1.5 1-2 2.2-2H18a3 3 0 0 0 3-3c0-5-4-9-9-9ZM7.5 12.5h.01M10 8h.01M15 8h.01" />,
  marketing: <path d="M4 10v4h3l7 4V6l-7 4H4ZM17 9a4 4 0 0 1 0 6" />,
  research: <><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></>,
  agents: <><circle cx="12" cy="8" r="3.5" /><path d="M5 20a7 7 0 0 1 14 0M9 8h.01M15 8h.01" /></>,
  artifacts: <path d="M6 3h8l4 4v14H6V3ZM14 3v4h4M9 12h6M9 16h6" />,
  memory: <path d="M12 4a4 4 0 0 0-4 4c-1.5.5-2.5 2-2.5 3.5S6.5 15 8 15.3V17a3 3 0 0 0 6 0M12 4a4 4 0 0 1 4 4c1.5.5 2.5 2 2.5 3.5S17.5 15 16 15.3M12 4v13" />,
  knowledge: <path d="M5 5.5A2.5 2.5 0 0 1 7.5 3H19v15H7.5A2.5 2.5 0 0 0 5 20.5V5.5ZM5 20.5A2.5 2.5 0 0 1 7.5 18H19v3H7.5A2.5 2.5 0 0 1 5 20.5Z" />,
  vault: <><rect x="4" y="5" width="16" height="14" rx="2" /><circle cx="12" cy="12" r="3.2" /><path d="M12 12h3.5" /></>,
  tasks: <path d="M5 6h.01M5 12h.01M5 18h.01M9 6h11M9 12h11M9 18h11" />,
  forge: <path d="m14 4 6 3-2.5 3.5L21 13l-4 4-6-6M11 11 4 18l2 2 7-7M9 7l2 2" />,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1" /></>,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></>,
  command: <path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6Z" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19" /></>,
  moon: <path d="M20 13.5A8 8 0 1 1 10.5 4a6.5 6.5 0 0 0 9.5 9.5Z" />,
  contrast: <><circle cx="12" cy="12" r="8.5" /><path d="M12 3.5v17a8.5 8.5 0 0 0 0-17Z" fill="currentColor" stroke="none" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  check: <path d="m5 12.5 4.5 4.5L19 7" />,
  alert: <path d="M12 3 2.5 20h19L12 3ZM12 10v4M12 17h.01" />,
  play: <path d="M7 5v14l11-7-11-7Z" />,
  arrowRight: <path d="M5 12h14M13 6l6 6-6 6" />,
  chevronRight: <path d="m9 6 6 6-6 6" />,
  chevronLeft: <path d="m15 6-6 6 6 6" />,
  chevronDown: <path d="m6 9 6 6 6-6" />,
  panelRight: <><rect x="3.5" y="5" width="17" height="14" rx="2" /><path d="M15 5v14" /></>,
  spark: <path d="M12 3s1 5 3 7 6 2 6 2-4 0-6 2-3 7-3 7-1-5-3-7-6-2-6-2 4 0 6-2 3-7 3-7Z" />,
  shield: <path d="M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6l-7-3ZM9 12l2 2 4-4" />,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3 2" /></>,
  dot: <circle cx="12" cy="12" r="3.5" fill="currentColor" stroke="none" />,
};

export type IconName = keyof typeof ICONS;
export const iconNames = Object.keys(ICONS) as IconName[];

export interface IconProps extends React.SVGProps<SVGSVGElement> {
  name: IconName;
  size?: number;
  /** When true (default) the icon is decorative and hidden from a11y tree. */
  decorative?: boolean;
  title?: string;
}

export function Icon({
  name,
  size = 20,
  decorative = true,
  title,
  ...rest
}: IconProps) {
  const glyph = ICONS[name] ?? ICONS.dot;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={decorative ? "presentation" : "img"}
      aria-hidden={decorative || undefined}
      aria-label={decorative ? undefined : title}
      {...rest}
    >
      {title && !decorative ? <title>{title}</title> : null}
      {glyph}
    </svg>
  );
}
