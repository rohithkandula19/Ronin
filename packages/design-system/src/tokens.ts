/**
 * RONIN DESIGN SYSTEM (RDS 1.0) — canonical design tokens.
 *
 * This file is the single source of truth for the "Sumi" (墨, ink) design
 * language: sumi-e ink on warm washi paper. Every token here has a matching
 * CSS custom property in `styles/rds.css` (prefixed `--rds-`). When the two
 * disagree, THIS FILE wins and `rds.css` must be updated to match — the
 * contract test in `tokens.test.mjs` guards a subset of that mapping.
 *
 * Nothing here is theme-aware: these are the *reference* values (light theme
 * defaults for semantic roles). Runtime theming (dark / oled / high-contrast)
 * is done in CSS via the `[data-theme]` attribute — see rds.css.
 */

// ─────────────────────────────────────────────────────────────── raw palette
// Warm neutral ramps + a single restrained clay accent. No blue-purple.

export const palette = {
  // Paper: warm near-whites → light greys (surfaces, borders in light theme).
  paper0: "#ffffff",
  paper50: "#fafaf9",
  paper100: "#f5f4f2",
  paper200: "#ecebe7",
  paper300: "#e7e5e4",
  paper400: "#d6d3cd",
  // Ink: warm greys → near-black (text, dark-theme surfaces).
  ink300: "#9b9793",
  ink400: "#6b6b6b",
  ink500: "#4a4744",
  ink700: "#2a2724",
  ink900: "#1a1a1a",
  ink950: "#0f0e0d",
  // Clay: the one accent. Warm, earthen, quiet.
  clay100: "#efe3d6",
  clay300: "#d4a373",
  clay500: "#a98467",
  clay700: "#8a6a4f",
  clay900: "#5f4838",
  // Semantic hues, warm-tuned so they sit beside clay without shouting.
  success: "#4f7a5b",
  successSoft: "#e4ede4",
  warn: "#b8863b",
  warnSoft: "#f3e7cf",
  danger: "#b4462f",
  dangerSoft: "#f2ddd6",
  info: "#4a6b82",
  infoSoft: "#dde7ee",
} as const;

// ─────────────────────────────────────────────────────────── semantic roles
// Light-theme mapping. The same role names are re-pointed per theme in CSS.

export const semantic = {
  bg: palette.paper50,
  surface: palette.paper0,
  surfaceRaised: palette.paper0,
  surfaceSunken: palette.paper100,
  border: palette.paper300,
  borderStrong: palette.paper400,
  text: palette.ink900,
  textDim: palette.ink400,
  textFaint: palette.ink300,
  accent: palette.clay500,
  accentSoft: palette.clay300,
  accentDeep: palette.clay700,
  accentTint: palette.clay100,
  onAccent: palette.paper0,
} as const;

// ───────────────────────────────────────────────────────────────── typography
// System stacks only — Ronin is local-first and offline-capable; no font fetch.

export const font = {
  sans: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, "Helvetica Neue", Arial, sans-serif',
  mono: 'ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Monaco, "Cascadia Code", monospace',
  serif: 'ui-serif, "Iowan Old Style", Georgia, "Times New Roman", serif',
} as const;

/** Type scale: [font-size rem, line-height, letter-spacing]. */
export const type = {
  display: { size: "3.5rem", leading: "1.05", tracking: "-0.02em", weight: 600 },
  h1: { size: "2.25rem", leading: "1.15", tracking: "-0.01em", weight: 600 },
  h2: { size: "1.75rem", leading: "1.2", tracking: "-0.01em", weight: 600 },
  h3: { size: "1.375rem", leading: "1.3", tracking: "0", weight: 600 },
  h4: { size: "1.125rem", leading: "1.4", tracking: "0", weight: 600 },
  bodyLg: { size: "1.0625rem", leading: "1.6", tracking: "0", weight: 400 },
  body: { size: "0.9375rem", leading: "1.6", tracking: "0", weight: 400 },
  sm: { size: "0.8125rem", leading: "1.5", tracking: "0", weight: 400 },
  caption: { size: "0.75rem", leading: "1.4", tracking: "0.01em", weight: 500 },
} as const;

export const weight = { regular: 400, medium: 500, semibold: 600, bold: 700 } as const;

// ───────────────────────────────────────────────────────────────── spacing
// 4px base unit. Names are the multiplier; values are px.

export const space = {
  0: "0px",
  1: "4px",
  2: "8px",
  3: "12px",
  4: "16px",
  5: "20px",
  6: "24px",
  8: "32px",
  10: "40px",
  12: "48px",
  16: "64px",
  20: "80px",
  24: "96px",
} as const;

export const radius = {
  sm: "6px",
  md: "10px",
  lg: "14px",
  xl: "20px",
  "2xl": "28px",
  full: "9999px",
} as const;

// Elevation: Sumi prefers borders. Shadows are warm-tinted and sparing.
export const elevation = {
  e0: "none",
  e1: "0 1px 2px rgba(26,24,22,0.06)",
  e2: "0 4px 16px rgba(26,24,22,0.08)",
  e3: "0 12px 40px rgba(26,24,22,0.12)",
} as const;

// ───────────────────────────────────────────────────────────────── motion
// Near-silent, purposeful. Nothing bounces; nothing lingers.

export const duration = {
  instant: "80ms",
  fast: "140ms",
  base: "220ms",
  slow: "360ms",
  deliberate: "500ms",
} as const;

export const easing = {
  standard: "cubic-bezier(0.2, 0.7, 0.2, 1)",
  entrance: "cubic-bezier(0, 0, 0.2, 1)",
  exit: "cubic-bezier(0.4, 0, 1, 1)",
} as const;

// ───────────────────────────────────────────────────────────────── layers
export const z = {
  base: 0,
  nav: 40,
  panel: 50,
  overlay: 100,
  command: 200,
  toast: 300,
} as const;

// Structural dimensions for the three-pane app shell.
export const layout = {
  railCollapsed: "72px",
  railExpanded: "260px",
  rightPanel: "360px",
  contentReading: "1200px",
  contentApp: "1440px",
} as const;

export const themes = ["light", "dark", "oled", "hc"] as const;
export type Theme = (typeof themes)[number];

/** The honest-status scheme. UI never invents a status outside this set. */
export const statusKinds = [
  "VERIFIED",
  "IMPLEMENTED",
  "DRAFT",
  "BLOCKED",
  "DISABLED",
] as const;
export type StatusKind = (typeof statusKinds)[number];

/** Safety/risk tone for world actions and tools. */
export const riskTones = ["safe", "review", "caution", "restricted"] as const;
export type RiskTone = (typeof riskTones)[number];

export const rds = {
  palette,
  semantic,
  font,
  type,
  weight,
  space,
  radius,
  elevation,
  duration,
  easing,
  z,
  layout,
  themes,
  statusKinds,
  riskTones,
} as const;

export default rds;
