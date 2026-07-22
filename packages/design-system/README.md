# RONIN DESIGN SYSTEM — RDS 1.0

> The **Sumi** (墨, ink) design language. Sumi-e ink on warm washi paper: warm
> neutrals, a single restrained clay accent, vast negative space, near-silent
> motion. The deliberate antithesis of the neon-gradient AI aesthetic.
>
> Ronin is a masterless expert. Its interface is a **dojo**, not a cockpit.

RDS is the permanent foundation for every Ronin surface. New features do not
invent colors, spacing, motion, or components — they compose these. This
package is the single source of truth; the design intent behind it lives in
[`docs/design/`](../../docs/design/).

## What's in the box

| Path | Purpose |
| --- | --- |
| `src/tokens.ts` | Canonical typed tokens (the source of truth). |
| `styles/rds.css` | Runtime token layer: CSS custom properties + all four themes. |
| `src/components/*` | React primitives (Button, Card, StatusLabel, RiskChip, …). |
| `src/index.ts` | Public entry — import tokens and primitives from here. |
| `src/tokens.test.mjs` | Drift guard: tokens.ts ↔ rds.css must agree. |

## Using it

```ts
// 1. Load the token layer once, before Tailwind, in your root:
import "@ronin/design-system/styles/rds.css";

// 2. Use primitives and tokens anywhere:
import { Button, StatusLabel, rds } from "@ronin/design-system";
```

Theming is a single attribute on `<html>`:

```html
<html data-theme="dark">   <!-- light | dark | oled | hc -->
```

With no `data-theme`, RDS follows the OS `prefers-color-scheme` (light → paper,
dark → sumi).

## Tokens at a glance

- **Color** — warm `paper` and `ink` ramps, one `clay` accent ramp
  (`#a98467`), warm-tuned semantic hues (success/warn/danger/info). Semantic
  roles (`--rds-bg`, `--rds-surface`, `--rds-border`, `--rds-text`,
  `--rds-accent`, …) are re-pointed per theme; raw ramps stay constant.
- **Type** — system sans for UI, `ui-monospace` for code (local-first: no font
  fetch). Scale: display → caption, tracked tight at display sizes.
- **Space** — 4px base unit (`--rds-space-1` … `-24`).
- **Radius** — `sm` 6 → `2xl` 28, plus `full`.
- **Elevation** — borders are preferred; shadows (`e1`–`e3`) are warm-tinted
  and sparing.
- **Motion** — durations `instant` 80ms → `deliberate` 500ms; easings
  `standard` / `entrance` / `exit`. Reduced-motion collapses to opacity/none.
- **Layers & layout** — z-index scale (nav→toast) and the three-pane shell
  dimensions (rail 72/260, right panel 360).

## The honest-status contract

`StatusLabel` renders only the sanctioned status vocabulary — `VERIFIED`,
`IMPLEMENTED`, `DRAFT`, `BLOCKED`, `DISABLED`. `RiskChip` renders only
`safe` / `review` / `caution` / `restricted`. These are components, not free
text, precisely so the product cannot drift into optimistic or invented
statuses. Truthful labeling is a design rule, enforced in code.

## Themes

| Theme | Feel | Use |
| --- | --- | --- |
| `light` | Warm washi paper | Default, daytime. |
| `dark` | Sumi ink | Low-light, focus. |
| `oled` | True black | Battery / OLED displays. |
| `hc` | Pure black on white, thick borders | WCAG AAA / accessibility. |

## Tests

```bash
node --test src/*.test.mjs   # drift guard + theme presence (12 checks)
```
