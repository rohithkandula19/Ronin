# 05 — Interaction & Motion

> RONIN DESIGN SYSTEM (RDS 1.0) — codename **Sumi** (墨, ink).
> Motion is the ink settling on the page, not a fireworks show.

This document defines how RDS surfaces move and respond: the motion philosophy, the
duration and easing tokens and exactly when to use each, the entrance/exit and feedback
patterns, how AI streaming and loading should *feel*, optimistic UI and rollback, focus
management, the approval flow, the CommandCenter choreography, and the complete
reduced-motion policy.

Every value here references RDS motion tokens. No component invents a duration or easing.

---

## 1. Philosophy — "near-silent, purposeful"

Sumi motion follows four principles:

1. **Near-silent.** The default is stillness. Motion appears only to explain a change of
   state, guide attention, or confirm an action. If a motion can be removed without losing
   meaning, remove it. There are no ambient/looping decorative animations anywhere except
   the Spinner (which is information).
2. **Purposeful.** Every animation answers a question the user is already asking: *Where
   did this come from? Where did it go? Did my action register? Is something happening?*
3. **Fast, then calm.** Feedback is instant (≤140ms); transitions of substance are calm
   (220–360ms). Nothing bounces, overshoots, or springs. Ink does not spring.
4. **Distance over flash.** Movement is small — 4 to 12px translations, subtle opacity.
   The eye should register that something moved, not watch it travel.

Anti-patterns, explicitly banned: parallax, neon glows, gradient sweeps, elastic/bounce
easings, staggered "reveal on scroll" cascades longer than 3 items, spinners as the
primary loading experience where a skeleton would tell more, and any motion that blocks
input.

---

## 2. Duration tokens — and when to use each

| Token | Value | Use for |
|-------|-------|---------|
| `--rds-duration-instant` | 80ms | Micro-feedback: press ripple-out, checkbox tick, hover color on small controls, toggle thumb start. |
| `--rds-duration-fast` | 140ms | Hover state changes, tooltip appear, focus ring, button/segment transitions, small fades. |
| `--rds-duration-base` | 220ms | The default for meaningful transitions: fadeUp entrances, panel content swaps, tab changes, most enter/exit. |
| `--rds-duration-slow` | 360ms | Larger surfaces entering: RightIntelligencePanel sheet, CommandCenter open, dialog/overlay. |
| `--rds-duration-deliberate` | 500ms | Rare, weighty transitions where the user must register consequence: a world switch, a destructive commit confirmation, first-run reveals. Use sparingly. |

Rule of thumb: the larger the surface and the more consequential the change, the longer
the duration — but never past `deliberate`. Small things move fast; big things move calmly.

---

## 3. Easing tokens — and when to use each

| Token | Curve | Use for |
|-------|-------|---------|
| `--rds-ease-standard` | `cubic-bezier(0.2,0.7,0.2,1)` | The default. Anything that moves both in and stays (hover, position change, size change, reordering). Gentle acceleration, soft settle. |
| `--rds-ease-entrance` | `cubic-bezier(0,0,0.2,1)` | Elements **entering** the screen. Decelerate-only: fast in, soft stop — arrives and settles, never overshoots. |
| `--rds-ease-exit` | `cubic-bezier(0.4,0,1,1)` | Elements **leaving** the screen. Accelerate-only: eases out and speeds away. Exits are slightly faster than their entrance (use one duration step down). |

Pairing convention: an element that fades up in with `base`/`entrance` fades down out with
`fast`/`exit`. Entrances are witnessed; exits get out of the way.

---

## 4. Core patterns

### 4.1 fadeUp (the signature entrance)
The house entrance for content appearing in place: cards, list rows, messages, panel
content, dialog bodies.

```
from { opacity: 0; transform: translateY(8px); }
to   { opacity: 1; transform: translateY(0); }
duration: var(--rds-duration-base);   /* 220ms */
easing:   var(--rds-ease-entrance);
```

- Translate distance: 8px default; 4px for small elements (tooltip, badge), 12px for large
  surfaces (panel). Never more than 12px.
- **Staggering:** for a list, stagger children by 24–40ms, capped at the first ~6 items;
  beyond that, appear together. A long list must not ripple.
- The reverse (`fadeDown` exit) uses `fast` + `exit`.

### 4.2 Reveal / collapse (height)
For expanding a disclosure, plan step detail, rail expand. Animate `grid-template-rows`
`0fr → 1fr` (or measured height) at `base`/`standard`, with content opacity fading in
after 40% of the duration so text doesn't smear.

### 4.3 Position/reorder
When items reorder (plan steps completing, list re-sort), use a FLIP transition at
`base`/`standard`. Items glide to new positions; they do not fade-and-reappear.

### 4.4 Cross-fade (content swap)
Swapping panel content for a new selection: outgoing fades at `fast`/`exit`, incoming
fadeUps at `base`/`entrance`, overlapping by ~half. No layout jump — reserve the space.

---

## 5. Hover & press feedback

- **Hover** (`fast`, `standard`): the surface shifts one paper step OR the border darkens
  one ink step — never both, never a scale. Icons shift `--rds-text-dim`→`--rds-text`.
- **Press/active** (`instant`, `standard`): the surface goes one step further and opacity
  dips to `.92`. No downward translate — presses are felt through color/opacity, not
  motion. The change is instant so the control feels rigid and responsive, like pressing
  a real key.
- **Focus-visible** (`fast`): the 2px `--rds-accent` ring at 2px offset fades in. It does
  not animate size (no expanding-ring effect).
- **Disabled**: no transition; state is static at `opacity: .45`.

---

## 6. Loading & AI streaming

Loading is a spectrum; pick the quietest tool that tells the most truth.

| Situation | Treatment |
|-----------|-----------|
| Action commit (button) | In-button `Spinner`, width preserved, `aria-busy`. No layout change. |
| Known-shape content loading | **Skeleton** (see §7), never a spinner. |
| Unknown/indeterminate wait | `Spinner` with a live-region label. |
| Streaming AI response | Token stream (see below). |

### 5-a. Token streaming — how it should feel
AI output arrives token-by-token. The feel we want is *ink appearing as it is written*,
calm and readable, not a stuttering ticker.

- **Append, don't reflow-flash.** New tokens append to the text node; existing text never
  re-lays-out or re-fades. The paragraph grows downward.
- **Caret.** A 2px `--rds-accent` block caret sits at the stream head, blinking at a slow
  `~1s` cadence (opacity, not motion). It marks "still writing." It vanishes the instant
  the stream ends.
- **No per-token animation.** Do NOT fade or slide each token — at token rates that reads
  as jitter. Tokens simply appear; the caret carries the sense of liveness.
- **Rhythm smoothing.** Render tokens on a ~16–33ms rAF cadence rather than exactly as
  they arrive over the wire, so bursty network delivery reads as steady writing.
- **Rich blocks** (code, tables, an ApprovalCard) that resolve mid-stream fadeUp in as a
  unit once complete, rather than assembling character by character.
- **Auto-scroll.** While pinned to bottom, the view follows the stream head with a smooth
  `base` scroll. If the user scrolls up, pinning releases and a "↓ resume" affordance
  appears; the stream keeps writing off-screen without yanking the viewport.
- **Completion.** Caret removed; if the message ends in a tool result or plan step, the
  relevant StatusLabel transitions (never straight to VERIFIED without proof — see §9 and
  `04 §3.10`).

---

## 7. Skeletons

Skeletons stand in for known content shapes (cards, message rows, artifact grid) during
first load.

- Geometry mirrors the real content (same radii, same spacing, same column count) so the
  transition to real content is a cross-fade, not a jump.
- Fill: `paper-100`; the shimmer is a slow opacity pulse between `paper-100` and `paper-200`
  at ~1.4s (opacity only — no moving gradient sweep; that violates §1). In `hc` and under
  reduced-motion the shimmer is a static `paper-200` block.
- Skeleton → content: real content fadeUps (`base`/`entrance`) as the skeleton fades out
  (`fast`/`exit`), overlapping. Never longer than the data actually takes; if data is
  already cached, skip the skeleton entirely.

---

## 8. Optimistic UI & rollback

For low-risk, high-confidence, reversible actions (toggling a setting, renaming, marking
read, reordering), commit optimistically:

1. **Apply immediately** with the normal transition; the UI reflects success at once.
2. **Mark provisional** subtly: the affected element carries a faint `--rds-text-dim`
   pending underline or a tiny inline `Spinner`; StatusLabel reads `PENDING`/`IMPLEMENTED`,
   not `VERIFIED`.
3. **On confirm:** drop the provisional marker (`fast` fade). If it warranted a StatusLabel,
   it may now move to `VERIFIED` (proof received).
4. **On failure — rollback:** the element animates back to its prior state via a FLIP/
   cross-fade at `base`, and a `danger`-tone Toast (z `300`) explains what failed with a
   retry. The rollback motion is deliberately visible (not instant) so the user perceives
   the reversal rather than being confused by a silent snap-back.

Never apply optimism to MEDIUM+ risk actions — those route through the ApprovalCard (§9)
and are never assumed to succeed.

---

## 9. The approval interaction flow

Approval-before-action is a first-class, motion-supported flow (component in `04 §4.8`).

1. **Halt.** When the agent proposes a MEDIUM+ risk action, streaming pauses and the
   PlanTracker's current step holds at `IMPLEMENTED`/`PENDING` — never advancing.
2. **Surface.** An `ApprovalCard` fadeUps (`base`/`entrance`, 12px) inline in the
   MessageList and is mirrored as a pulse-once badge on the RightIntelligencePanel. The
   card carries a RiskChip and the concrete, enumerated effects.
3. **Announce & focus.** The card is `aria-live="assertive"`; focus moves to it (to the
   Reject button by default — the safe choice is the default). `Esc` maps to Reject.
4. **Decide.**
   - `Approve` — the card collapses (`fast`/`exit`), the plan step shows a `Spinner`, and
     the action executes. On completion the step's StatusLabel transitions to `VERIFIED`
     only if verified, else `IMPLEMENTED`.
   - `Reject` — the card collapses; the step moves to `BLOCKED_INPUT` with the user's
     reason; the agent replans.
5. **CRITICAL gate.** For `CRITICAL` risk, `Approve` is a `danger`-variant button and is
   disabled until a typed confirmation (e.g. the resource name) matches or re-auth
   completes. The deliberate friction is intentional; the motion here uses `deliberate`
   timing to signal weight.

No motion shortcut, timeout, or auto-approve exists. The agent physically cannot proceed
past an unresolved ApprovalCard.

---

## 10. CommandCenter open/close choreography

The ⌘K CommandCenter (z `command`, 200) is the most-used transition in the product, so it
is tuned carefully.

**Open** (triggered by `⌘K` / `Ctrl K`):
1. Scrim fades in `paper`/`ink` at ~40% opacity, `fast`/`entrance`.
2. The command surface fadeUps 12px + scales from `.98`→`1`, `slow`(360ms)/`entrance`.
   Scale is the only place a subtle scale is permitted, and it is tiny.
3. Focus lands in the input immediately (before the animation ends — never make the user
   wait to type). Keystrokes typed during the entrance are captured.
4. Results list is present but empty→populates with a capped stagger (§4.1) as the user
   types; async results show an inline `Spinner`.

**Close** (`Esc`, selection, or scrim click):
1. Surface fades down 8px + no scale, `base`(220)/`exit` — exit is quicker than entrance.
2. Scrim fades out `fast`/`exit`.
3. Focus returns to the element that had it before open (focus restoration, §11 / `06 §4`).

Selecting a command that navigates: the CommandCenter closes on the exit path *while* the
destination content fadeUps in beneath — the two overlap so it feels like the command
delivered you there, not two separate transitions.

---

## 11. Focus management

Motion and focus are coordinated so keyboard users are never lost.

- **On overlay open** (CommandCenter, dialog, ApprovalCard, right-panel sheet): focus moves
  into the surface (to the safest control), and a focus trap engages. This happens on open,
  independent of the entrance animation's completion.
- **On close:** focus restores to the triggering element. If that element no longer exists
  (e.g. it was removed by the action), focus falls back to the nearest logical landmark.
- **On route/content swap:** focus moves to the new content's heading (`tabindex=-1`) and
  is announced; the viewport does not steal focus mid-stream during AI output.
- **Focus is never animated away from the viewport.** If focus moves to an element that is
  off-screen, the container scrolls it into view at `base`/`standard` first.
- Focus-visible rings appear instantly-ish (`fast`) and are exempt from being suppressed by
  reduced-motion (they must always be visible).

---

## 12. Z-index & layering during motion

Transitions respect the z stack so nothing flickers through the wrong layer:
`base 0 → nav 40 → panel 50 → overlay 100 → command 200 → toast 300`. Entering overlays
raise their layer *before* animating; exiting overlays animate first, then drop. Toasts
always win and enter from the top-right with a `fast`/`entrance` fadeUp, auto-dismiss with
`exit`, and pause their timer on hover/focus.

---

## 13. Reduced-motion policy

When `prefers-reduced-motion: reduce` is set (or the user enables it in RDS settings), the
system honors it globally — components do not each re-implement this.

The policy, precisely:

- **Translations, scales, and slides are removed.** `transform`-based movement (fadeUp,
  panel slide, command scale, thumb travel, FLIP reorder) is replaced by an instant
  position change with, at most, an **opacity** cross-fade at `fast`.
- **Opacity fades are kept but shortened.** A fade may remain (it does not induce motion
  sickness) but is capped at `fast` (140ms) or less. This preserves the sense of
  appear/disappear without movement.
- **Looping motion stops.** The Spinner becomes a static pulsing-opacity dot; the streaming
  caret stops blinking (stays solid); skeleton shimmer becomes a static block.
- **Auto-scroll becomes instant.** Stream follow and scroll-into-view jump rather than
  glide.
- **Essential motion exception.** Where movement is the only way to convey meaning (e.g. a
  rollback reversal, §8), it is retained but minimized to a brief opacity cross-fade —
  meaning is never sacrificed, only decoration.
- **Focus rings are never reduced.** They always render.

Implementation: a single global rule zeroes transition/animation *durations* on transform
properties and swaps loop keyframes; opacity transitions clamp to `fast`. Because every
component names motion tokens rather than hard-coding, honoring reduced-motion is a
token-layer switch, not a per-component rewrite.

```css
@media (prefers-reduced-motion: reduce) {
  :root {
    --rds-duration-base: 0ms;
    --rds-duration-slow: 0ms;
    --rds-duration-deliberate: 0ms;
    /* fast retained for opacity-only fades */
  }
  *, *::before, *::after {
    animation-iteration-count: 1 !important;
    transition-property: opacity !important; /* drop transform transitions */
  }
}
```

---

## 14. Interaction timing summary

| Interaction | Duration | Easing | Movement |
|-------------|----------|--------|----------|
| Button/segment hover | `fast` | standard | color only |
| Button press | `instant` | standard | opacity dip, no translate |
| Focus ring appear | `fast` | standard | opacity |
| Tooltip appear | `fast` | entrance | fadeUp 4px |
| Card/row entrance | `base` | entrance | fadeUp 8px |
| List stagger step | +24–40ms | entrance | (capped ~6) |
| Disclosure expand | `base` | standard | height + delayed opacity |
| Panel content swap | `fast`+`base` | exit→entrance | cross-fade |
| Right panel sheet (mobile) | `slow` | entrance/exit | slide+fade 12px |
| CommandCenter open | `slow` | entrance | fadeUp 12px + scale .98→1 |
| CommandCenter close | `base` | exit | fadeDown 8px |
| ApprovalCard appear | `base` | entrance | fadeUp 12px |
| Optimistic rollback | `base` | standard | FLIP/cross-fade (kept under RM) |
| Toast in / out | `fast` | entrance/exit | fadeUp from top-right |
| World switch | `deliberate` | standard | cross-fade |
| Spinner | 1.4s loop | linear | rotate (dot pulse under RM) |
| Stream caret | ~1s | — | opacity blink (solid under RM) |
