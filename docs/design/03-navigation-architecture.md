# 03 — Navigation Architecture

> How you move through Ronin. This document specifies the three-pane shell, the
> left rail, the center workspace, the right intelligence panel, the Command
> Center (CMD+K), the global keyboard map, navigation state and deep-linking,
> and responsive collapse order.
>
> It is the behavioral companion to `02-information-architecture.md`, which
> defines *what* the places are. This document defines *how you get between
> them*. Motion and surface treatment follow the **Sumi** identity: warm washi
> paper, a single clay accent (`#a98467`), vast negative space, near-silent
> motion. Nothing here should feel like a neon SaaS dashboard.

---

## 1. The three-pane shell

```
┌────────┬───────────────────────────────────────┬──────────────────┐
│  LEFT  │                                        │      RIGHT       │
│  RAIL  │            CENTER WORKSPACE            │  INTELLIGENCE    │
│        │                                        │      PANEL       │
│ 72 or  │   the active surface: Home, a world,   │  Context /       │
│ 260px  │   a conversation, canvas, code,        │  Memory /        │
│        │   artifacts                            │  Provenance /    │
│  nav   │                                        │  Plan & Agents / │
│        │   ← breadcrumb ─────────────────────   │  Approvals       │
│        │   ← mode switcher (in a world)         │                  │
│        │                                        │  0 / 340 / 420px │
└────────┴───────────────────────────────────────┴──────────────────┘
                  ▲ CMD+K overlays the whole shell
```

- **Left rail** — where you *are* in the product (worlds + global
  destinations). Persistent.
- **Center workspace** — the active surface. The only pane that changes
  identity as you navigate.
- **Right intelligence panel** — *why* the center looks the way it does:
  context, memory, provenance, plan/agent activity, approvals. Collapsible.

The shell is stable: navigating changes the center (and re-scopes the right
panel), but the rail never disappears except in the narrowest responsive tier
(§9). Panes are separated by hairline dividers, not heavy chrome — the washi
ground shows through.

---

## 2. Left rail

The rail is the product's spine. Two states: **collapsed (72px, icons)** and
**expanded (260px, icons + labels)**.

### 2.1 Contents (top to bottom)

| Zone | Item | Collapsed (72px) | Expanded (260px) |
|---|---|---|---|
| Mark | Ronin mark | glyph only | glyph + wordmark |
| Global | Home | icon | icon + "Home" |
| — | Command Center trigger | icon (⌘) | icon + "Search & command" + `⌘K` hint |
| Worlds | section label | thin divider | "WORLDS" caption |
| — | Enabled worlds (Coding, Education, Healthcare …) | world glyph | glyph + name + health dot |
| — | "All worlds" → `/worlds` | grid icon | icon + "All worlds" |
| Lenses | Memory, Agents, Artifacts, Knowledge, Tasks | icons | icons + labels |
| System | Vault, Forge | icons | icons + labels |
| Footer | Account / avatar | avatar | avatar + name + status |
| — | Settings | gear | gear + "Settings" |

- **World health dot** uses the honesty labels: solid clay = healthy/enterable;
  hollow = disabled future; amber ring = `UNHEALTHY`/`EVAL_FAILING` (not
  enterable). Disabled worlds remain visible (fail-closed IA), never removed.
- Worlds section scrolls independently if the enabled set is long; global and
  system zones are pinned.

### 2.2 Collapsed vs. expanded

- **Default:** collapsed (72px) on first run — negative space is a feature, not
  a bug. The rail earns its width only when you ask.
- **Toggle:** `[` collapses / expands; also a hover-peek — hovering the
  collapsed rail for >400ms slides a 260px overlay *without* reflowing the
  center (peek, not pin). Clicking the pin icon commits the width.
- **Persistence:** committed width is remembered per device.
- **Motion:** width transitions are near-silent — 180ms, ease-out, no bounce.
  The wordmark and labels cross-fade; they do not slide in with momentum.

### 2.3 Interaction states

| State | Treatment |
|---|---|
| Rest | icon in ink-muted; no background |
| Hover | faint washi-warm background wash; label appears (collapsed peek) |
| Active (current place) | clay accent left-marker (2px) + icon in full ink; background a shade warmer |
| Focus (keyboard) | 2px clay focus ring, offset; never removed for mouse users |
| Pressed | 60ms darken, no ripple |
| Disabled world | 40% opacity, cursor default, tooltip with reason |

Exactly **one** active item at a time. Being inside a world's sub-mode still
highlights the world (the mode is shown in the center's mode switcher, not the
rail).

### 2.4 Keyboard navigation within the rail

- `⌘1`…`⌘9` — jump to the Nth pinned destination (Home = `⌘1`, then worlds, then
  lenses, in rail order).
- `g` then a letter — "go to" chords: `g h` Home, `g w` Worlds, `g m` Memory,
  `g a` Agents, `g t` Tasks, `g v` Vault, `g f` Forge.
- `Tab` / `Shift+Tab` move through rail items in DOM order; `Enter` activates;
  `↑`/`↓` move within the focused zone (roving tabindex, so the rail is a single
  tab stop from outside).
- `[` toggles collapse from anywhere that isn't a text field.

---

## 3. Center workspace

The center hosts whatever surface the current route resolves to. It is
**single-surface by default** — one primary thing at a time — with scoped tabs
only where a workflow genuinely needs parallel objects.

### 3.1 Single-surface vs. tabs

- **Single-surface is the default.** Ronin favors focus and negative space over
  a tab-forest. Navigating replaces the center surface; history (§8) lets you
  return.
- **Tabs appear only inside a world**, and only for concurrent *objects* of the
  same kind you're actively juggling (e.g. two open conversations, a
  conversation + its canvas). Tabs are world-scoped and do not persist a giant
  session graveyard — they cap at a small set and offer "open in Artifacts /
  Conversations list" beyond that.
- Global lenses (Artifacts, Agents, Memory) are **never** tabbed; they are
  single-surface lists that open detail in place or in the right panel.

### 3.2 Breadcrumbs

A single breadcrumb line sits atop the center:

```
Coding  ›  Conversation  ›  "Refactor auth module"
```

- First crumb is always the world (or the global lens name). Clicking it goes
  to World Home / lens root.
- Crumbs are truncated middle-first on narrow widths; the last crumb (current
  object) is never truncated away.
- The breadcrumb reflects route depth from §5 of the IA doc; it is the visible
  echo of the URL.

### 3.3 Switching a world's interaction modes

Inside a world, the modes — **chat, canvas, artifacts, agents, actions,
knowledge, visualization** — are switched by a **mode switcher** at the top of
the center (a segmented control under the breadcrumb), not by the left rail.

- Rationale: the rail answers "which world / which place"; the mode switcher
  answers "how am I working *in* this world." Keeping modes out of the rail
  prevents the rail from ballooning per-world.
- Mode switches are route changes (`/worlds/[world]/canvas/...` etc.), so they
  are deep-linkable and history-tracked.
- Keyboard: `⌘⌥1..7` switch modes in the fixed order above; the switcher shows
  the digit hints on `⌘⌥`-hold.
- The current mode persists per world, so re-entering a world returns you to
  how you last worked there (World Home redirect, IA §5).

### 3.4 Center empty/loading

- Loading uses a quiet skeleton on washi ground — no spinner theatrics, in
  keeping with near-silent motion.
- Empty surfaces show the honest, place-specific empty states from IA §7, plus
  the world's suggested next actions.

---

## 4. Right intelligence panel

The panel explains and steers the center. Five sections, in fixed order.

| Section | Shows | Auto-behavior |
|---|---|---|
| **Context** | What Ronin is currently attending to: active world, model/provider in use, open object, attached knowledge. | Always available; top section. |
| **Memory** | World-scoped memory items in play, each visible and revocable inline. | Reflects active world's Vault namespace; re-scopes on world switch. |
| **Provenance** | For the focused artifact/answer: source, model, inputs, tools/retrieval used, honesty label. | Populates when an object is focused. |
| **Plan & Agents** | Live plan steps, running agents, task progress. | Streams while agents run; quiet when idle. |
| **Approvals** | Pending gated actions awaiting a human decision. | **Auto-opens the panel** when an approval is pending. |

### 4.1 Collapsible behavior

- Three widths: **collapsed (0 / a 12px seam), standard (340px), wide
  (420px)**. `]` toggles collapsed ↔ last-open width; drag the seam to resize.
- Collapsed leaves a thin seam with section dots that pulse *only* when
  something wants attention (an agent finished, provenance is available). The
  clay accent is the only color used for these signals.
- Per-place memory of open/closed: the panel remembers whether you like it open
  in, say, Coding vs. Home.

### 4.2 Auto-open policy (the one interruption we allow)

The panel **auto-opens on a pending Approval**, and only then, because
fail-closed safety must be *seen*, not buried. Everything else (agent finished,
new provenance) uses the quiet seam pulse — it invites, it does not interrupt.
An auto-open focuses the Approvals section and keyboard focus lands on the
primary decision control, so a keyboard user can approve/deny without reaching
for the mouse. Dismissing without deciding re-collapses the panel but leaves the
seam pulsing and the action still blocked.

### 4.3 Panel ↔ center relationship

- Selecting an item in the panel (a memory item, an agent run, a provenance
  source) can navigate the center or open a detail route — the panel drives the
  center, never a modal stack.
- The panel is world-scoped: switching worlds re-scopes Memory/Context/Safety;
  Plan & Agents and Approvals show the active world by default with an
  "all worlds" toggle mirroring the lens scope param (`?scope=all`).

---

## 5. Command Center (CMD+K)

The universal entry point. One input, four modes, ranked results, closes back
to where you were.

### 5.1 Invocation

- `⌘K` (or `Ctrl K`) from anywhere opens it as a centered overlay on a dimmed
  washi scrim.
- `Esc` closes and returns focus to the prior element. It never navigates on
  close.
- It reads the **current world** as context, so results and actions are ranked
  for where you are.

### 5.2 Modes

The mode is inferred from a leading sigil or from the query; you can also `Tab`
between modes explicitly.

| Mode | Sigil / trigger | Does | Example |
|---|---|---|---|
| **Navigate** | default / `>` | Jump to any IA node (worlds, lenses, objects, settings). | `coding`, `> memory` |
| **Act** | `/` | Run a command/action in the current world (world-local actions first, then global). | `/new conversation`, `/export artifact` |
| **Ask** | `?` | Ask Ronin a one-shot question using the current world's tools/model. | `? what changed in this repo today` |
| **Run agent** | `@` | Invoke a configured agent, optionally with an argument. | `@researcher summarize sources` |

### 5.3 Result ranking

Results are grouped and ranked so the *right* answer is first without reading:

1. **Exact/pinned** — direct route or exact object name match.
2. **In-world context** — actions, agents, objects belonging to the current
   world (context boost).
3. **Recent & frequent** — objects and destinations you touched lately.
4. **Cross-world matches** — same match in other worlds, clearly world-labeled.
5. **Fallback to Ask** — if nothing matches, the top row offers "Ask Ronin: …"
   so a query is never a dead end.

Each row shows its world badge and, for Act/Run, the autonomy/approval
consequence (e.g. "requires approval") — you see the safety cost *before* you
hit `Enter`.

### 5.4 Keyboard model

- `↑`/`↓` move selection; `Enter` runs the selected result; `⌘Enter` runs it in
  a new tab/background where applicable (e.g. run an agent without leaving the
  current surface).
- `Tab` cycles modes; `Backspace` on an empty query drops the mode sigil back to
  Navigate.
- Type-ahead is debounced quietly; no results flicker (min-display window so the
  list doesn't thrash).

### 5.5 How it composes with the panes

- **Navigate** emits a route change → the **center** swaps and the **right
  panel** re-scopes.
- **Act / Run agent** may raise an **Approval** → the **right panel** auto-opens
  (§4.2) rather than blocking inside the overlay.
- **Ask** streams into the center (a lightweight conversation in the current
  world) with **Provenance** populating in the right panel.
- The Command Center never becomes a place (no route); it always dissolves back
  to the shell it was invoked from.

---

## 6. Global keyboard map

Shortcuts are chord- and vim-flavored where it aids muscle memory. All are
disabled while a text input/textarea has focus except the reserved global set
(`⌘K`, `Esc`, `⌘\`).

| Shortcut | Action | Scope |
|---|---|---|
| `⌘K` / `Ctrl K` | Open Command Center | Global (reserved) |
| `Esc` | Close overlay / cancel / defocus | Global (reserved) |
| `[` | Toggle left rail collapse | Global |
| `]` | Toggle right panel collapse | Global |
| `⌘\` | Toggle both side panes (focus mode) | Global (reserved) |
| `⌘1`…`⌘9` | Jump to Nth rail destination | Global |
| `g h` | Go to Home | Chord |
| `g w` | Go to Worlds | Chord |
| `g m` | Go to Memory | Chord |
| `g a` | Go to Agents | Chord |
| `g r` | Go to Artifacts | Chord |
| `g t` | Go to Tasks | Chord |
| `g v` | Go to Vault | Chord |
| `g f` | Go to Forge | Chord |
| `⌘⌥1`…`⌘⌥7` | Switch world mode (chat…visualization) | In a world |
| `⌘[` / `⌘]` | Back / Forward in nav history | Global |
| `⌘N` | New conversation in current world | In a world |
| `⌘⇧A` | Focus Approvals (if pending) | Global |
| `⌘,` | Open Settings | Global |
| `?` | Show keyboard help sheet | Global (non-input) |
| `j` / `k` | Move down / up in a list or lens | List context |
| `Enter` | Open focused list item | List context |
| `x` | Select/multi-select item | List context |

The `?` help sheet lists these contextually — global always, plus the chords
valid for the current place. Shortcuts are user-remappable in
`/settings/appearance` (or via the harness keybindings for power users);
defaults above are the shipped map.

---

## 7. Focus mode

`⌘\` collapses both side panes to give the center full width — for deep work in
canvas or code. The rail becomes the 12px seam; the right panel becomes its
seam (still pulsing for approvals). `⌘\` again restores the previous widths.
This is the one gesture that momentarily suspends the three-pane shell, and it
is fully reversible.

---

## 8. Navigation state & deep-linking

### 8.1 Everything addressable is a URL

Per IA §5, every place and object has a stable route. Consequences for nav:

- **Deep links restore full context.** Opening
  `/worlds/coding/c/abc?panel=provenance&scope=all` restores the world, the
  conversation, the right panel open on Provenance, and the widened lens scope.
- **Ephemeral state rides in query params**, not history-defining paths:
  `?panel=`, `?mode=`, `?scope=`, `?q=` (Command Center pre-fill). These update
  via `replaceState` so they don't spam the back stack.
- **Identity-defining changes push history**: switching world, opening an
  object, changing mode.

### 8.2 Back / forward behavior

- `⌘[` / `⌘]` (and browser back/forward) walk the route history: world →
  conversation → artifact detail unwinds cleanly.
- **The Command Center overlay is not a history entry** — closing it with `Esc`
  or `back` returns to the underlying place, it doesn't pop a phantom step.
- **Panel open/close and mode-within-panel are not separate history entries**
  (they're `replaceState`), so back never just closes a panel — it returns to a
  real prior place. This is deliberate: back should move you between *places*,
  not toggle UI.
- Restoring a session (relaunch) reopens the last route with panel/scope params
  intact — local-first means your place survives a restart without a network
  round-trip.

### 8.3 Cross-links

- Right-panel items deep-link into the center (a provenance source → the
  Knowledge source route; an agent run → `/agents/[run]`).
- A world-local lens link (`/worlds/[world]/artifacts`) and the global lens
  (`/artifacts?world=[world]`) resolve to the same filtered view — the world
  path is the canonical, shareable form.

---

## 9. Responsive collapse order

Ronin is desktop-first (it is an OS shell) but must degrade gracefully. Panes
fold in a fixed priority so the *center always survives*.

| Tier | Width | Left rail | Center | Right panel |
|---|---|---|---|---|
| **Full** | ≥ 1280px | expanded or collapsed (user choice) | full | open (340/420) |
| **Comfort** | 1024–1279px | **auto-collapse to 72px** | full | open, standard (340) |
| **Compact** | 768–1023px | 72px | full | **collapse to seam**; opens as overlay on demand |
| **Narrow** | 480–767px | **collapse to seam / top bar**; rail opens as a left drawer | full | overlay drawer from right |
| **Mobile** | < 480px | bottom tab bar (Home / Worlds / Command / Memory) | full-screen surface | full-screen sheet |

Collapse priority, first to fold:

1. **Right panel → seam** (its content is available on demand; the center can
   stand alone). Approvals still force it open as an overlay when pending.
2. **Left rail → seam/drawer** (Command Center + `g` chords cover navigation
   when the rail is a drawer).
3. **Breadcrumb truncates** middle-first; mode switcher condenses to a dropdown.
4. The **center never collapses** — it is the reason the app exists.

On touch tiers, `CMD+K` becomes a persistent search affordance in the top/bottom
bar, since there is no keyboard to summon it. Hover-peek is replaced by tap-to-
open-drawer. Motion stays near-silent; drawers slide at 200ms ease-out with a
scrim, no spring.

---

## 10. Navigation anti-patterns (refused)

- **No modal stacks.** The right panel and Command Center replace deep modals;
  we never trap the user under three dialogs.
- **No hamburger-only nav on desktop.** The rail is present; hiding is the
  user's choice or a responsive necessity, never the default desktop pattern.
- **No back-button surprises.** Back moves between places, never merely toggles
  a pane.
- **No hidden safety.** An approval always surfaces (auto-open); we never let an
  agent proceed on a gated action while the request is off-screen.
- **No motion theater.** Transitions are ≤ 200ms, ease-out, no bounce/parallax/
  neon — Sumi means near-silent.

---

## 11. Cross-references

- Places, routes, object model, worlds model: `02-information-architecture.md`.
- Sumi identity, motion tokens, color (`#a98467`), spacing: design system
  (`01-*`).
- Fail-closed world loading and autonomy levels:
  `docs/product/ronin-ai-os-vision.md`.
- Approvals, autonomy, and audit substrate: `packages/vault`, `docs/safety.md`.
