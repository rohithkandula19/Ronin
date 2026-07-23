import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";

/**
 * apps/web Tailwind theme is a thin projection of the RONIN DESIGN SYSTEM
 * (RDS 1.0). Colors resolve to `--rds-*` custom properties defined in
 * `@ronin/design-system/styles/rds.css`, so every utility is theme-aware
 * (light / dark / oled / hc) automatically. Legacy names (bg, ink, dim,
 * border, accent) are preserved so existing pages keep working.
 */
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "../../packages/design-system/src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // legacy semantic names (kept)
        bg: "var(--rds-bg)",
        ink: "var(--rds-text)",
        dim: "var(--rds-text-dim)",
        border: "var(--rds-border)",
        accent: {
          DEFAULT: "var(--rds-accent)",
          soft: "var(--rds-accent-soft)",
          deep: "var(--rds-accent-deep)",
          tint: "var(--rds-accent-tint)",
        },
        // RDS semantic roles
        surface: {
          DEFAULT: "var(--rds-surface)",
          raised: "var(--rds-surface-raised)",
          sunken: "var(--rds-surface-sunken)",
        },
        text: {
          DEFAULT: "var(--rds-text)",
          dim: "var(--rds-text-dim)",
          faint: "var(--rds-text-faint)",
        },
        "border-strong": "var(--rds-border-strong)",
        "on-accent": "var(--rds-on-accent)",
        // status hues + soft tints
        success: { DEFAULT: "var(--rds-success)", soft: "var(--rds-success-soft)" },
        warn: { DEFAULT: "var(--rds-warn)", soft: "var(--rds-warn-soft)" },
        danger: { DEFAULT: "var(--rds-danger)", soft: "var(--rds-danger-soft)" },
        info: { DEFAULT: "var(--rds-info)", soft: "var(--rds-info-soft)" },
        // a couple of raw ramp stops used directly by primitives
        "ink-300": "var(--rds-ink-300)",
      },
      fontFamily: {
        sans: ["var(--rds-font-sans)"],
        mono: ["var(--rds-font-mono)"],
        serif: ["var(--rds-font-serif)"],
      },
      maxWidth: {
        reading: "var(--rds-content-reading)",
        app: "var(--rds-content-app)",
      },
      transitionTimingFunction: {
        standard: "var(--rds-ease-standard)",
        entrance: "var(--rds-ease-entrance)",
        exit: "var(--rds-ease-exit)",
      },
    },
  },
  plugins: [typography],
};

export default config;
