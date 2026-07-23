# 06 — Accessibility & Responsive

> RONIN DESIGN SYSTEM (RDS 1.0) — codename **Sumi** (墨, ink).
> Accessibility is not a theme; it is the floor every theme stands on.

This document defines the accessibility contract (WCAG 2.2 AA) and the responsive behavior
of RDS, with particular attention to the two hardest problems: the three-pane AppShell and
the ⌘K CommandCenter.

Accessibility is a release gate, not a nice-to-have. A component that fails these criteria
does not ship.

---

## 1. Target: WCAG 2.2 AA

RDS conforms to **WCAG 2.2 Level AA** as the baseline, with several AAA behaviors adopted
where they are cheap and high-value. The success criteria we treat as first-class:

| SC | Name | RDS commitment |
|----|------|----------------|
| 1.4.3 | Contrast (min) | All text meets 4.5:1 (large text / UI 3:1). §2 |
| 1.4.11 | Non-text contrast | Borders, focus rings, control boundaries ≥3:1. §2 |
| 1.4.13 | Content on hover/focus | Tooltips dismissible, hoverable, persistent. |
| 2.1.1 / 2.1.2 | Keyboard / no trap | Everything operable by keyboard; traps only where intended and escapable. §4 |
| 2.4.3 | Focus order | DOM order matches visual/reading order. §4 |
| 2.4.7 | Focus visible | 2px accent ring, always. §3 |
| 2.4.11 | Focus not obscured (min) | Focused element never hidden behind sticky panels/overlays. §3 |
| 2.5.8 | Target size (min) | Interactive targets ≥24×24; RDS default ≥44×44. §7 |
| 3.2.2 | On input | No context change on focus/input without warning. |
| 3.3.x | Error identification/suggestion | Field errors are text + programmatic. §5 |
| 4.1.2 / 4.1.3 | Name/role/value + status messages | Correct roles; live regions for status. §5, §6 |

New in 2.2 and explicitly covered: **2.4.11 focus-not-obscured** (critical with our sticky
three-pane layout) and **2.5.8 target size**.

---

## 2. Contrast

All role-token pairings are verified to meet or exceed AA. Key results against the RDS
palette:

| Foreground | Background | Ratio | Use | Result |
|-----------|-----------|-------|-----|--------|
| `ink-900` #1a1a1a | `paper-50` #fafaf9 | ~16.8:1 | body text (light) | AAA |
| `ink-400` #6b6b6b | `paper-50` #fafaf9 | ~5.0:1 | dim text (light) | AA |
| `paper-100` #f5f4f2 | `ink-950` #0f0e0d | ~17.5:1 | body text (dark) | AAA |
| `paper-0` #ffffff | `clay-500` #a98467 | ~3.3:1 | text on accent fill | — see note |
| `clay-700` #8a6a4f | `paper-50` | ~4.6:1 | accent text on paper | AA |
| `paper-300` #e7e5e4 | `paper-50` | ~1.1:1 | hairline border | non-text 3:1? see note |

### 2.1 The clay accent problem, solved honestly
`clay-500` (#a98467) is a warm mid-tone. White text on it lands around **3.3:1** — enough
for large/bold text and UI component boundaries (SC 1.4.11, 3:1) but **short of 4.5:1 for
normal body text**. RDS therefore rules:

- Filled `primary` buttons use **≥ `body` weight 600 at ≥16px equivalent**, qualifying as
  large-scale text (AA large = 3:1). Small text is never placed on a clay fill.
- For accent-colored **text on paper**, use `clay-700` (#8a6a4f, ~4.6:1), not `clay-500`.
  `clay-500` is a fill/graphic color, not a text color on light surfaces.
- Focus rings and borders use `clay-500` — they are non-text (3:1 bar), which it clears.

### 2.2 Hairline borders
`paper-300` on `paper-50` is a decorative hairline below the 3:1 non-text threshold. This
is permitted **only** for purely aesthetic separation. Any border that conveys state or
boundary of an interactive control (input border, selected marker, focus ring) uses a
token that meets 3:1 (`ink-400`+ or `clay-500`). In `hc` theme all borders resolve to
pure black/white at 2px and clear contrast trivially.

### 2.3 High-contrast (`hc`) theme
`[data-theme="hc"]` remaps: `--bg`/`--surface` pure white (or black in dark hc), `--text`
pure black/white (21:1), `--border` pure black/white at **2px** thickness, `--accent` to a
darkened clay that clears 4.5:1 as text. Semantic colors shift to their darkest variants.
`hc` is offered as a user choice and is also the fallback when the OS reports
`forced-colors: active` — in forced-colors mode RDS lets system colors through and only
ensures structure (borders present, focus visible) survives.

### 2.4 Color is never the only signal
Enforced from `04 §6.4`: RiskChip, StatusLabel, semantic Badge, and Field error each carry
a **glyph and/or text label** in addition to color. This guarantees meaning survives
grayscale, color-blindness, and forced-colors. Verified against protanopia/deuteranopia/
tritanopia simulations.

---

## 3. Focus visibility

- **Ring:** 2px solid `--rds-accent` at 2px offset, applied via `:focus-visible` only
  (pointer clicks do not summon the ring; keyboard/AT navigation does).
- **Contrast:** the ring clears 3:1 against every theme background; in `hc` it is 3px and
  pure-color.
- **Never suppressed:** reduced-motion does not remove rings; `outline: none` without a
  replacement is a lint error.
- **Not obscured (SC 2.4.11):** because we have sticky rail/panel/headers, focused elements
  are scrolled into a clear region — `scroll-margin` is set on focusable content so a
  sticky header never covers the focused row. Overlays that open trap focus *inside*, so
  focus is never behind a scrim.
- **Focus restoration:** closing any overlay returns focus to its trigger (see `05 §11`).

---

## 4. Keyboard operability

Everything is operable without a pointer. Global and per-pattern maps:

### 4.1 Global
| Key | Action |
|-----|--------|
| `⌘K` / `Ctrl K` | Open CommandCenter |
| `Esc` | Close top-most overlay / dismiss tooltip / cancel |
| `Tab` / `Shift+Tab` | Move through focus order (matches DOM/reading order) |
| `⌘\` / `Ctrl \` | Toggle LeftRail collapsed/expanded |
| `⌘.` / `Ctrl .` | Toggle RightIntelligencePanel |
| `F6` / `Ctrl F6` | Move focus between the three landmark panes |

### 4.2 Three-pane AppShell
The three panes are landmark regions (§5). `F6` cycles focus **between** panes (rail →
main → panel → rail) so keyboard users move between regions in one keystroke rather than
tabbing through everything. Within a pane, `Tab` moves normally. The center `main` is first
in DOM/reading order despite the rail rendering visually left — no, correction: DOM order
is rail → main → panel (matching left-to-right visual order and SC 2.4.3), and a
"skip to main content" link is the first focusable element on the page.

### 4.3 CommandCenter (⌘K)
| Key | Action |
|-----|--------|
| `↑` / `↓` | Move through results |
| `Enter` | Run highlighted command |
| `⌘Enter` | Run in a specific mode (where applicable) |
| `Esc` | Close, restore focus to trigger |
| `Tab` | Move between input and grouped sections (focus-trapped inside) |
It is a modal dialog (`role="dialog"`, `aria-modal="true"`), focus-trapped, labelled by its
input. The result list uses `role="listbox"`/`option` with `aria-activedescendant` so the
input keeps focus while arrows move the visual selection, and the active option is announced.

### 4.4 Other patterns
- **LeftRail:** roving `tabindex` down nav items; `Enter`/`Space` activate; collapsed icons
  expose their label via `aria-label` + Tooltip (which is keyboard-triggerable).
- **RightIntelligencePanel / PlanTracker:** list semantics; plan steps are not buttons
  unless actionable.
- **ApprovalCard:** focus moves in on appear, defaults to the safe (Reject) control, `Esc`
  = Reject, `Enter` on a focused button activates it. Approve for CRITICAL is unreachable
  until the confirmation field validates.
- **ModePicker:** `role="radiogroup"`, arrow keys move selection.
- **MessageList:** arrow/scroll navigable; a "jump to latest" control is keyboard-reachable.
- **Toggle/Field:** native/`switch` semantics, fully keyboard-native.

No keyboard trap exists except intentional modal traps, all of which are escapable with
`Esc` (SC 2.1.2).

---

## 5. Screen-reader semantics

### 5.1 Landmarks for the three panes
The AppShell exposes exactly one of each primary landmark so AT users get a clean rotor:

| Region | Element / role | Accessible name |
|--------|---------------|-----------------|
| Top bar | `<header>` `role="banner"` | — |
| LeftRail | `<nav>` `role="navigation"` | `aria-label="Primary"` |
| Center content | `<main>` `role="main"` | `aria-label` = current world/route |
| RightIntelligencePanel | `<aside>` `role="complementary"` | `aria-label="Intelligence"` |
| Toasts | `role="region"` `aria-label="Notifications"` | — |

A visually-hidden "Skip to main content" link is the first tab stop.

### 5.2 Live regions
| Content | Politeness | Notes |
|---------|-----------|-------|
| AI token stream | `aria-live="polite"` `aria-busy` while writing | Announced in readable chunks, not per-token; on completion, `aria-busy` clears and the final message is available to re-read. |
| Plan step status change | `polite` | StatusLabel changes announce e.g. "Step 2: verified". |
| ApprovalCard appearance | `assertive` | Interrupts — the user must know an action awaits them; focus also moves in (§4.4). |
| Field error | `role="alert"` | Announced immediately on validation. |
| Toast | `polite` (`assertive` for `danger`) | Errors interrupt; info does not. |
| Loading (page-level Spinner) | `polite` off-screen label | "Loading …" / "Loaded". |

Streaming announcement discipline: raw token-by-token output would flood a screen reader.
RDS buffers the stream and updates the live region on sentence/clause boundaries (or ~1s
idle), so the SR hears coherent phrases. `aria-busy="true"` during the stream tells AT the
region is still changing.

### 5.3 Names, roles, values
- Every IconButton has a required `label` → `aria-label` (`04 §3.2`); an unnamed icon
  control fails the build.
- StatusLabel and RiskChip expose their meaning as text to AT even when the visual is
  glyph-forward (the label text is real text, not `aria-label` on an icon).
- Field auto-wires `label`/`aria-describedby`/`aria-invalid` (`04 §3.12`).
- Decorative glyphs are `aria-hidden`; meaningful glyphs have text equivalents.

---

## 6. Reduced motion (a11y view)

Full policy in `05 §13`. From the accessibility standpoint: RDS honors
`prefers-reduced-motion: reduce` globally, replacing transform-based motion with instant
changes plus short opacity fades, stopping all looping animation (spinner → static dot,
caret stops blinking, skeleton stops shimmering), and making auto-scroll instant. Focus
rings are exempt and always render. This satisfies the intent of SC 2.3.3 (Animation from
Interactions, AAA) which RDS adopts.

---

## 7. Hit-target sizes (SC 2.5.8)

- **Default interactive target: ≥44×44px.** Buttons at `md` render 36px tall visually but
  carry a transparent inset that extends the pointer/tap target to 44px (`04 §2.3`).
- **Absolute minimum: 24×24px** for dense, inline, or exception cases (e.g. an inline
  overflow dot in a table row), and only when spacing keeps neighboring targets from
  overlapping the 24px exclusion — matching SC 2.5.8's "target offset" exception.
- **Spacing:** adjacent targets keep ≥8px (`space-2`) gap so fat-finger errors are rare.
- IconButtons are square with the 44px target regardless of icon size.
- Touch breakpoints (compact/tablet, §8) raise `sm` controls to `md` sizing so nothing is
  below 44px on touch.

---

## 8. Responsive breakpoints

RDS is desktop-first (it is an OS-like app shell) but fully adapts down to phone. Four
breakpoints:

| Name | Range | Primary device |
|------|-------|----------------|
| `compact` | < 640px | Phone |
| `tablet` | 640–1024px | Tablet / small window |
| `desktop` | 1024–1440px | Laptop / standard |
| `wide` | > 1440px | Large monitor |

### 8.1 How the three-pane shell adapts

| Breakpoint | LeftRail | Center content | RightIntelligencePanel | Notes |
|-----------|----------|----------------|------------------------|-------|
| `wide` (>1440) | Expanded 260px (user can collapse) | max 1440px app / 1200px reading, centered | Docked 360px, always visible | Full three-pane. Extra width becomes margin (negative space is a feature). |
| `desktop` (1024–1440) | Collapsed 72px by default, expandable | Fills remaining width, max applies | Docked 360px, visible | The canonical layout. Panel can be toggled with `⌘.`. |
| `tablet` (640–1024) | Collapsed 72px, icon-only; expands as temporary overlay | Full width minus rail | **Overlay sheet** from right, 360px (or 90vw), over a scrim; not docked | Two-pane feel; panel summoned on demand, elevation `e3`, focus-trapped while open. |
| `compact` (<640) | Hidden; becomes a bottom tab bar OR a hamburger-triggered overlay drawer | Full-bleed, single column, edge padding `space-4` | **Full-screen sheet** (100vw) sliding up/over; dismiss returns to content | Single-pane. Rail and panel are both summoned surfaces, never simultaneous with content space. CommandCenter (⌘K, or a persistent search affordance) becomes the primary navigation. |

### 8.2 Behavioral rules across breakpoints
- **Rail:** docked+expandable ≥1024; icon-only rail ≥640; off-canvas <640. Expanding the
  rail at `tablet` overlays content rather than pushing it (avoids reflow of the work area).
- **Right panel:** docked ≥1024; becomes an overlay **sheet** below 1024 (right-anchored on
  tablet, full-screen on compact) with scrim, `e3` elevation, focus trap, and `Esc` to
  close — reusing the CommandCenter overlay machinery.
- **When a panel/rail is an overlay,** opening it does not shift the underlying content
  (no layout thrash); it floats above with the scrim. The center content remains the
  scroll context.
- **Content max-width:** `1200px` for reading-dominant routes (MessageList, docs),
  `1440px` for app-dominant routes (Artifacts gallery grid). Beyond that, center and let
  the washi margin breathe.
- **Grids** (WorldCard, ArtifactCard) reflow: 1 col compact → 2 col tablet → 3 col desktop
  → 4 col wide, using `auto-fill minmax()` so it degrades gracefully at odd widths.
- **Touch targets:** below 1024 (touch-likely), all `sm` controls promote to `md`; hover-
  only affordances (e.g. card hover reveals) get a persistent visible equivalent since
  touch has no hover.
- **Text:** the type scale does not shrink below `desktop`; instead line-length is
  constrained by content max-width. On `compact`, `display`/`h1` may step down one scale
  level to avoid wrapping awkwardly, via a fluid clamp — but body text never goes below
  `body` (0.9375rem) for legibility.

### 8.3 Orientation & zoom
- Layout reflows to **400% zoom** (SC 1.4.10) without horizontal scrolling of the page body
  — wide content (tables, code, diagrams) scrolls within its own container, never the page.
- No content is locked to a single orientation.
- Respects OS text-size / `rem`-based sizing throughout; nothing is sized in `px` where the
  user's font preference should scale it.

---

## 9. Testing & the accessibility gate

A component or template ships only after:

- [ ] Automated: axe / equivalent passes with zero violations in all four themes.
- [ ] Contrast: every text/UI pairing verified ≥ AA (§2), clay rules honored.
- [ ] Keyboard: full task completion with no pointer, focus order correct, no unintended
      traps, all overlays `Esc`-dismissible.
- [ ] Screen reader: landmarks present and unique, live regions announce correctly (stream
      buffered, approvals assertive), names/roles correct — tested on VoiceOver + NVDA.
- [ ] Focus visible everywhere, never obscured by sticky panes (SC 2.4.11).
- [ ] Reduced-motion path verified; forced-colors mode structurally intact.
- [ ] Hit targets ≥44px (≥24px only where the offset exception applies).
- [ ] Responsive: verified at 375 / 768 / 1280 / 1920 px and at 400% zoom.

Accessibility failures block release. There is no "fix it later" lane.
