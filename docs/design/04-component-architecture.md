# 04 — Component Architecture

> RONIN DESIGN SYSTEM (RDS 1.0) — codename **Sumi** (墨, ink).
> Sumi-e ink on warm washi paper. Warm neutrals, a single clay accent, vast negative
> space, near-silent motion. Anti neon-gradient.

This document defines the component system: the taxonomy (Primitives → Patterns →
Templates), the API and naming conventions every component obeys, the rules for
composition, and the decision test for when a thing earns its place as a primitive.

All visual values reference RDS tokens exactly. A component never hard-codes a hex,
a pixel radius, or a duration — it consumes `--rds-*` custom properties so that theme
(`light` / `dark` / `oled` / `hc`) resolves for free.

---

## 1. Taxonomy

RDS is a three-tier system. Each tier has a different rate of change, a different owner,
and a different bar for admission.

| Tier | What it is | Composes | Ships in | Change cadence |
|------|-----------|----------|----------|----------------|
| **Primitive** | Single-responsibility building block. No product knowledge. | Tokens only | `@rds/primitives` | Rare, reviewed hard |
| **Pattern** | An opinionated arrangement of primitives that solves a recurring product problem (a rail, a panel, a card). | Primitives + patterns | `@rds/patterns` | Steady |
| **Template** | A full-page assembly wiring patterns to routes and data. | Patterns | app `features/` | Fast, product-owned |

The rule of dependency direction: **primitives never import patterns; patterns never
import templates.** Dependencies point down the tier list only. A violation is a lint
error, not a style opinion.

```
Templates      Home · World workspace · Artifacts gallery
   │  (compose)
Patterns       AppShell · LeftRail · RightIntelligencePanel · CommandCenter
   │           WorldCard · ArtifactCard · PlanTracker · ApprovalCard · MessageList · ModePicker
   │  (compose)
Primitives     Button · IconButton · Surface · Card · Panel · Separator · Kbd · Badge
   │           RiskChip · StatusLabel · Avatar · Field · Toggle · Tooltip · Spinner
   │  (consume)
Tokens         --rds-* (color · type · space · radius · elevation · motion · z)
```

---

## 2. Naming & API conventions

These conventions are non-negotiable; consistency is the feature.

### 2.1 Component naming
- **PascalCase** for components (`RiskChip`), **camelCase** for props (`isLoading`).
- Compound components use dot notation for parts that are meaningless alone:
  `Field.Label`, `Field.Input`, `Field.Hint`, `Field.Error`; `Card.Header`,
  `Card.Body`, `Card.Footer`.
- A boolean prop reads as an assertion: `disabled`, `loading`, `selected`, `dimmed`.
  Never `isDisabled` in markup-facing APIs — the `is`/`has` prefix is reserved for
  internal state variables, not the public surface.

### 2.2 The shared prop vocabulary
Every primitive draws props from one dictionary so that learning one teaches the rest.

| Prop | Type | Meaning |
|------|------|---------|
| `variant` | enum | The intent/emphasis axis (`primary` \| `secondary` \| `ghost` \| `danger`). |
| `size` | enum | `sm` \| `md` \| `lg`; `md` is always the default. |
| `tone` | enum | Semantic color role (`neutral` \| `success` \| `warn` \| `danger` \| `info` \| `accent`). |
| `state` | derived | Never a prop. Expressed via DOM (`:hover`, `:focus-visible`, `[data-state]`, `[aria-disabled]`). |
| `asChild` | bool | Render polymorphically into the child element (Radix-style slot) instead of the default tag. |
| `elevation` | enum | `e0`–`e3`. Defaults to `e0`; borders are preferred over shadows. |

### 2.3 Sizing tokens (shared by all interactive primitives)

| `size` | Height | Inline pad | Font token | Radius |
|--------|--------|-----------|-----------|--------|
| `sm` | 28px | `space-3` (12) | `sm` (0.8125) | `radius-sm` (6) |
| `md` | 36px | `space-4` (16) | `body` (0.9375) | `radius-md` (10) |
| `lg` | 44px | `space-5` (20) | `body-lg` (1.0625) | `radius-md` (10) |

Note: `md` height is 36px for visual density, but the **hit target** is padded to a
minimum of 44×44 via a transparent inset (see `06 — Accessibility §7`). Density is a
look; the tap target is a law.

### 2.4 States, once, for everything
Every interactive primitive implements the full state set. Missing states are the most
common bug, so they are enumerated here as a checklist every component must satisfy:

`default` · `hover` · `active` (pressed) · `focus-visible` · `disabled` · `loading` ·
(where applicable) `selected` / `checked`.

- **hover** — surface shifts one paper step (e.g. `paper-0`→`paper-100`) OR border
  darkens one ink step. Never both.
- **active** — translate `1px` down is forbidden; instead the surface goes one step
  further and `opacity` dips to `.92`. Presses are felt, not seen (see `05 — Motion`).
- **focus-visible** — 2px ring in `--rds-accent` at 2px offset. Only on keyboard focus.
- **disabled** — `opacity: .45`, `pointer-events: none`, `aria-disabled="true"`.
- **loading** — content stays laid out (no reflow); a `Spinner` overlays and the control
  is `aria-busy="true"` and non-submittable.

---

## 3. Primitives

Fifteen primitives. Each entry: purpose, props/variants, states, and the tokens it
consumes. Tokens are listed by role name (`--rds-*`); the raw value lives in the token
sheet, never in the component.

### 3.1 Button
Purpose: the primary means of committing an action. The workhorse.

| Prop | Values | Default |
|------|--------|---------|
| `variant` | `primary` \| `secondary` \| `ghost` \| `danger` | `secondary` |
| `size` | `sm` \| `md` \| `lg` | `md` |
| `loading` | bool | `false` |
| `disabled` | bool | `false` |
| `iconStart` / `iconEnd` | node | — |
| `asChild` | bool | `false` |

Variants:
- `primary` — filled `--rds-accent` (clay-500) surface, `paper-0` text.
- `secondary` — `--rds-surface` fill, `--rds-border` 1px, `--rds-text`.
- `ghost` — transparent, no border; hover fills `paper-100`.
- `danger` — `danger` fill for destructive commits only.

States: default/hover/active/focus/disabled/loading (per §2.4). `loading` swaps the
label region for a `Spinner` sized to the text, preserving width.

Tokens: `--rds-accent`, `--rds-accent-700` (hover on primary), `--rds-surface`,
`--rds-border`, `--rds-text`, `--rds-danger`, radius `md`/`sm`, space `3`–`5`,
type `body`, motion `fast` + `standard`.

### 3.2 IconButton
Purpose: a Button whose entire label is a single icon (rail toggles, close, overflow).

| Prop | Values | Default |
|------|--------|---------|
| `variant` | `ghost` \| `secondary` \| `danger` | `ghost` |
| `size` | `sm` \| `md` \| `lg` | `md` |
| `label` | string (**required**) | — |
| `pressed` | bool \| undefined | — |

`label` is required and becomes `aria-label` — an IconButton with no accessible name is
a build error. When it toggles, pass `pressed` to emit `aria-pressed`. Square footprint
(height = width), always ≥44px hit target.

Tokens: same family as Button minus the fill logic; icon color = `--rds-text-dim`
default, `--rds-text` on hover.

### 3.3 Surface
Purpose: the atomic themed background primitive. Every visible container is ultimately a
Surface. It resolves `--rds-surface` / `--rds-bg` and optional border + elevation.

| Prop | Values | Default |
|------|--------|---------|
| `as` | element | `div` |
| `level` | `bg` \| `surface` \| `raised` | `surface` |
| `bordered` | bool | `false` |
| `elevation` | `e0`–`e3` | `e0` |
| `radius` | `sm`–`2xl` \| `none` | `none` |

States: static (non-interactive). Tokens: `--rds-bg`, `--rds-surface`,
`--rds-border`, radius scale, elevation scale.

### 3.4 Card
Purpose: a bordered, padded Surface for a discrete unit of content. The default
container for lists of things.

| Prop | Values | Default |
|------|--------|---------|
| `interactive` | bool | `false` |
| `elevation` | `e0`–`e2` | `e0` |
| `padding` | `space-4`–`space-6` | `space-5` |

Parts: `Card.Header`, `Card.Body`, `Card.Footer`. When `interactive`, the whole card is
a single focusable/clickable region with hover (border darkens one ink step) and
focus-visible ring; nested interactive controls must use `stopPropagation` and are
excluded from the card's activation.

Tokens: `--rds-surface`, `--rds-border`, radius `lg`, space `4`–`6`, elevation `e1`/`e2`,
motion `fast`.

### 3.5 Panel
Purpose: a full-height Surface region that docks to an edge (the right intelligence
panel, an inspector). Distinct from Card: Panel owns a viewport axis and can scroll
internally with a sticky header/footer.

| Prop | Values | Default |
|------|--------|---------|
| `side` | `left` \| `right` | `right` |
| `width` | length | `360px` |
| `resizable` | bool | `false` |

Parts: `Panel.Header` (sticky), `Panel.Body` (scroll), `Panel.Footer` (sticky).
Tokens: `--rds-surface`, `--rds-border` (single hairline against content), z-index
`panel` (50), space `5`, elevation `e0` on desktop / `e3` when it becomes an overlay
sheet (see `06 §8`).

### 3.6 Separator
Purpose: a 1px hairline dividing content. The most-used and least-noticed primitive.

| Prop | Values | Default |
|------|--------|---------|
| `orientation` | `horizontal` \| `vertical` | `horizontal` |
| `spacing` | space token | `space-4` |

Renders `role="separator"`. Tokens: `--rds-border`. In `hc` theme the border thickens
to 2px automatically via the token.

### 3.7 Kbd
Purpose: renders a keyboard key or chord (`⌘ K`, `Esc`). Non-interactive.

| Prop | Values | Default |
|------|--------|---------|
| `keys` | string[] | — |

Monospace stack, `sm` type, `paper-100` fill, `paper-300` border, radius `sm`. Used
heavily in CommandCenter and Tooltip shortcut hints. Tokens: `--rds-surface`,
`--rds-border`, `--rds-text-dim`, mono font, type `sm`, radius `sm`.

### 3.8 Badge
Purpose: a small count/label token attached to another element. Neutral by default.

| Prop | Values | Default |
|------|--------|---------|
| `tone` | `neutral` \| `accent` \| `info` \| `success` \| `warn` \| `danger` | `neutral` |
| `variant` | `soft` \| `solid` \| `outline` | `soft` |

`soft` uses the tint variant of the semantic color as fill with the full color as text.
Tokens: the semantic pair (`--rds-{tone}` + `--rds-{tone}-tint`), radius `full`,
type `caption`, space `1`/`2`.

### 3.9 RiskChip
Purpose: **encodes the safety/risk tone of an action or artifact.** A specialized Badge
carrying Ronin's safety vocabulary. Never decorative — its color IS information.

| Prop | Values | Default |
|------|--------|---------|
| `level` | `SAFE` \| `LOW` \| `MEDIUM` \| `HIGH` \| `CRITICAL` | — (required) |
| `size` | `sm` \| `md` | `sm` |

| `level` | Tone | Fill / Text | Meaning |
|---------|------|-------------|---------|
| `SAFE` | success | success-tint / `success` | Read-only, reversible, no side effects. |
| `LOW` | info | info-tint / `info` | Minor writes, easily undone. |
| `MEDIUM` | warn | warn-tint / `warn` | External side effects; confirm advised. |
| `HIGH` | danger | danger-tint / `danger` | Irreversible or costly; approval required. |
| `CRITICAL` | danger (solid) | `danger` / `paper-0` | Destructive/privileged; approval + re-auth. |

Always paired with a text label, never color-only, so it survives `hc` theme and
color-blind users (`06 §2`). Tokens: semantic `success`/`warn`/`danger`/`info` + tints,
radius `full`, type `caption`, weight `600`.

### 3.10 StatusLabel
Purpose: **encodes the honest-status scheme** — Ronin's discipline of never overclaiming
what it has actually verified. This is a load-bearing trust primitive.

| Prop | Values | Default |
|------|--------|---------|
| `status` | `VERIFIED` \| `IMPLEMENTED` \| `BLOCKED_INPUT` \| `BLOCKED_AUTH` \| `BLOCKED_ERROR` \| `PENDING` | — (required) |
| `detail` | string | — |

| `status` | Tone | Glyph | Claim it makes |
|----------|------|-------|----------------|
| `VERIFIED` | success | ✓ (check) | Ran and the result was checked against ground truth. Highest trust. |
| `IMPLEMENTED` | info | ○ (ring) | Code/action exists and ran, but the outcome was **not** independently verified. |
| `PENDING` | neutral | · (dot) | Not yet started or in queue. |
| `BLOCKED_INPUT` | warn | ⧗ | Waiting on the user for missing information. |
| `BLOCKED_AUTH` | warn | ⚿ | Waiting on credentials/permission/approval. |
| `BLOCKED_ERROR` | danger | ! | Halted by an error; needs intervention. |

The distinction between `VERIFIED` and `IMPLEMENTED` is the whole point: Ronin must not
paint an unverified action green. `VERIFIED` is the only success-tone status; everything
short of proof is info, warn, or danger. The glyph + label are both rendered so meaning
never rides on color alone. `detail` renders as a `caption` line beneath (e.g. the
verification method, or what is being waited on).

Tokens: semantic `success`/`info`/`warn`/`danger` + neutral (`--rds-text-dim`),
type `sm` + `caption`, weight `500`/`600`, space `1`/`2`, radius `sm`.

### 3.11 Avatar
Purpose: identity glyph for a user, agent, or world.

| Prop | Values | Default |
|------|--------|---------|
| `size` | `sm` (24) \| `md` (32) \| `lg` (40) | `md` |
| `src` | url | — |
| `name` | string | — (initials fallback) |
| `shape` | `circle` \| `square` | `circle` |

Fallback: initials on `clay-100` fill, `clay-700` text. `square` (radius `md`) is used
for agents/worlds; `circle` for humans. Tokens: `--rds-clay-100`, `--rds-clay-700`,
radius `full`/`md`, type `sm`.

### 3.12 Field / Input
Purpose: the text-entry compound. `Field` owns label/hint/error wiring; `Field.Input`
is the control.

| Prop (Field) | Values | Default |
|------|--------|---------|
| `invalid` | bool | `false` |
| `required` | bool | `false` |
| `size` | `sm` \| `md` \| `lg` | `md` |

Parts: `Field.Label`, `Field.Input`, `Field.Hint`, `Field.Error`. `Field` auto-generates
ids and wires `htmlFor`, `aria-describedby`, and `aria-invalid` across the parts — the
consumer never manages ids. On `invalid`, border → `danger`, `Field.Error` announced via
`role="alert"`.

States: default/hover(border darkens)/focus(accent ring, border→accent)/disabled/invalid.
Tokens: `--rds-surface`, `--rds-border`, `--rds-accent`, `--rds-danger`,
`--rds-text`/`--rds-text-dim`, radius `md`, space `3`/`4`, type `body`, motion `fast`.

### 3.13 Toggle
Purpose: a boolean switch for immediate, self-committing settings (not form submission).

| Prop | Values | Default |
|------|--------|---------|
| `checked` | bool | `false` |
| `size` | `sm` \| `md` | `md` |
| `disabled` | bool | `false` |

Track `paper-300`→`--rds-accent` on. Thumb `paper-0`, `e1`. `role="switch"`,
`aria-checked`. Motion: thumb translate at `fast` / `standard` easing; reduced-motion
snaps instantly. Tokens: `--rds-accent`, `--rds-paper-300`, `--rds-paper-0`,
elevation `e1`, motion `fast`.

### 3.14 Tooltip
Purpose: a transient text hint on hover/focus. Never contains interactive content.

| Prop | Values | Default |
|------|--------|---------|
| `content` | node | — |
| `side` | `top`\|`right`\|`bottom`\|`left` | `top` |
| `delay` | ms | `500` |

`role="tooltip"`, wired via `aria-describedby`; appears on hover AND keyboard focus,
dismissible with `Esc` (WCAG 1.4.13). Fill `ink-900` / text `paper-100` in light theme
(inverse), `caption` type. Motion: fadeUp 4px at `fast`/`entrance`. Tokens: `--rds-ink-900`,
`--rds-paper-100`, radius `sm`, elevation `e2`, type `caption`, motion `fast`,
z-index `overlay` (100).

### 3.15 Spinner
Purpose: indeterminate progress. The only continuously-animating primitive.

| Prop | Values | Default |
|------|--------|---------|
| `size` | `sm` (16) \| `md` (20) \| `lg` (24) | `md` |
| `tone` | `accent` \| `current` | `current` |

A single rotating arc, `1.4s` linear, 1.5px stroke — quiet, not a beach ball. Under
reduced-motion it becomes a static pulsing-opacity dot. Always paired with an
`aria-live="polite"` label off-screen when it represents page-level loading.
Tokens: `--rds-accent` or `currentColor`, motion (exempt from duration scale — it loops).

---

## 4. Patterns

Patterns solve recurring product problems. Each is documented by role, composition, and
the behaviors that make it more than the sum of its primitives. (Full interaction
choreography lives in `05 — Interaction & Motion`.)

### 4.1 AppShell (three-pane)
The root layout. Three regions: **LeftRail** (nav), **content** (center, the world/route),
**RightIntelligencePanel** (context). Owns the responsive collapse logic (`06 §8`),
landmark roles (`banner`/`navigation`/`main`/`complementary`), and the CommandCenter
mount point. Grid: `[rail] 1fr [panel]`. Content max-width `1200px` reading / `1440px` app.
Consumes: Surface, Separator, z-index `nav`/`panel`.

### 4.2 LeftRail
Primary navigation. `72px` collapsed (icons only) / `260px` expanded (icon + label).
Composed of IconButton (collapsed) / Button-ghost rows (expanded), Avatar (world switcher
at top), Separator, Tooltip (on collapsed icons). Selected item: `clay-100` fill +
2px `--rds-accent` left marker. z-index `nav` (40).

### 4.3 RightIntelligencePanel
Contextual intelligence for the active world: live status, plan, artifacts, risk.
A `Panel` (`side=right`, `360px`) hosting PlanTracker, StatusLabel stacks, RiskChips, and
ArtifactCard previews. Becomes a right-anchored overlay sheet below `1024px` (`06 §8`).
z-index `panel` (50) docked / `overlay` (100) as sheet.

### 4.4 CommandCenter
The ⌘K command surface — search, run, navigate, invoke. A modal `overlay` dialog
(z-index `command`, 200) with a Field.Input, grouped result list, Kbd hints, and inline
RiskChip on actionable commands. Focus-trapped, `Esc` to close, arrow-key navigation.
Open/close choreography in `05 §10`. Composed of: Field, Kbd, RiskChip, StatusLabel,
Separator, Spinner (async results).

### 4.5 WorldCard
Entry tile for a "world" (a workspace/agent context) on Home. `Card` (`interactive`)
with Avatar (square), title (`h4`), a StatusLabel for current world state, a RiskChip if
a pending high-risk action exists, and a `caption` last-activity line. Hover darkens
border + reveals a `ghost` "Open" affordance.

### 4.6 ArtifactCard
Represents a produced artifact (doc, dataset, deploy, PR). `Card` with a type glyph,
title, StatusLabel (VERIFIED/IMPLEMENTED critical here), size/time `caption`, and overflow
IconButton. Selectable within the Artifacts gallery grid. Preview thumbnail uses Surface
`raised`.

### 4.7 PlanTracker
The agent's plan as an ordered, honest checklist. Each step is a row: StatusLabel (the
step's true state) + step title + optional RiskChip. The current step gets an
`--rds-accent` left marker and a Spinner while running. This is where the honest-status
scheme is most visible — steps do not flip to VERIFIED until proof exists. Composed of:
StatusLabel, RiskChip, Spinner, Separator.

### 4.8 ApprovalCard
**Encodes approval-before-action.** When the agent proposes a MEDIUM+ risk action it
halts and renders an ApprovalCard: a summary of the proposed action, a prominent RiskChip,
the concrete effects ("will send email to 3 recipients", "will delete branch `x`"), and a
two-button commit row — `Approve` (primary, or `danger` variant when risk is HIGH+) and
`Reject` (secondary). CRITICAL actions additionally require a typed confirmation or
re-auth (`05 §9`). The card is `aria-live="assertive"` on appearance and focus moves to it.
The agent CANNOT proceed until resolved. Composed of: Card, RiskChip, Button, Kbd,
StatusLabel, Separator.

### 4.9 MessageList
The conversational transcript between user and agent. Alternating message rows (Avatar +
content), streaming assistant messages (token stream — `05 §5`), inline ApprovalCards and
PlanTrackers, and tool-call disclosures. Auto-scrolls while pinned to bottom; releases
pin on user scroll-up. Composed of: Avatar, Surface, Spinner, ApprovalCard, PlanTracker.

### 4.10 ModePicker
Selects the agent's operating mode (e.g. Ask / Plan / Auto / Free). A segmented control
of Button-ghost items with one `selected`, each carrying a RiskChip-style tone hint
(Auto/Free skew warmer = more autonomy = more risk). Emits `aria-pressed` per segment,
`role="radiogroup"`. Changing to a higher-autonomy mode surfaces a one-time confirmation.

---

## 5. Templates

Templates wire patterns to routes and data. They own layout choices and data-fetching, not
new visual vocabulary.

### 5.1 Home
The landing surface. AppShell with content = a WorldCard grid (responsive columns), a
"resume" strip of recent worlds, and an empty-state that invites creating the first world.
The RightIntelligencePanel shows global status (running agents across worlds). ⌘K is the
primary way to jump anywhere.

### 5.2 World workspace
The core working surface for one world. AppShell with content = MessageList (the primary
column) and the RightIntelligencePanel pinned to PlanTracker + artifacts + risk for this
world. ModePicker sits in the content header. ApprovalCards interrupt inline in the
MessageList and are mirrored as a badge on the panel.

### 5.3 Artifacts gallery
A filterable grid of ArtifactCards for a world (or across worlds). Content = filter bar
(by type, by StatusLabel state) + ArtifactCard grid; RightIntelligencePanel becomes an
inspector for the selected artifact (full StatusLabel history, provenance, RiskChip,
download/open). Grid reflows per breakpoint (`06 §8`).

---

## 6. Composition rules

1. **Consume tokens, never values.** A component that references `#a98467` or `220ms`
   directly fails review. Use `--rds-accent`, `--rds-duration-base`.
2. **One emphasis per view.** At most one `primary` Button in a visible region. If two
   things compete for "the" action, the design is wrong, not the button.
3. **Borders before shadows.** Reach for elevation (`e1`–`e3`) only for genuinely floating
   surfaces (overlays, sheets, menus). Docked surfaces get a hairline.
4. **Color is never the only signal.** Every RiskChip / StatusLabel / semantic Badge
   carries a glyph or text. This is a hard rule from `06 §2`, enforced here at the source.
5. **State lives in the DOM.** Hover/focus/active/checked are `:pseudo` / `[data-state]` /
   `[aria-*]`, not JS-driven className soup. This keeps SSR, theming, and a11y honest.
6. **Compose, don't fork.** Need a slightly different card? Compose Card + a prop, or use
   `asChild`. Do not copy Card into `SpecialCard`.
7. **Motion obeys `05`.** No component defines its own durations/easings; it names motion
   tokens. Reduced-motion is handled centrally, not per-component.

---

## 7. Primitive vs. one-off: the decision test

Promote something to a **primitive** only if it passes all four:

1. **Reused ≥3×** across unrelated features (the rule of three).
2. **No product knowledge** — it doesn't know about "worlds", "artifacts", or a specific
   API. (Product knowledge → it's a Pattern, not a Primitive.)
3. **Single responsibility** — it does one thing; if you're tempted to add a second
   `variant` axis for an unrelated concern, it's two components.
4. **Token-only styling** — it can be fully themed by `--rds-*` with zero overrides.

If it fails #2 but passes the rest, it's a **Pattern**. If it's used once and is unlikely
to recur, keep it a **one-off** in the feature folder — do not tax the design system with
speculative generality. A one-off that later hits the rule of three graduates through
review into `@rds/patterns` or `@rds/primitives`.

The cost of a wrong primitive is paid by everyone, forever; the cost of a one-off is paid
once, locally. When in doubt, keep it a one-off.

---

## 8. Component checklist (Definition of Done)

A component is not "done" until:

- [ ] All applicable states implemented (§2.4) and visible in stories.
- [ ] Styling is token-only; verified across `light`/`dark`/`oled`/`hc`.
- [ ] Keyboard operable; focus-visible ring present; `44px` hit target met.
- [ ] Accessible name + roles/`aria-*` correct (`06 §5`).
- [ ] Reduced-motion path verified (`05 §11`).
- [ ] No color-only meaning (§6.4).
- [ ] Public API uses the shared vocabulary (§2.2); no leaking hexes/px in props.
