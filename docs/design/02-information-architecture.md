# 02 — Information Architecture

> Ronin AI OS is an operating system, not a chatbot. Its information
> architecture is the map of *places you can be* and *things you can hold*, not
> a list of screens. This document defines the top-level tree, the Worlds
> model, the object model, the route architecture, the cross-cutting surfaces,
> and the empty/offline/first-run states.
>
> Status labels used here follow the house convention: **IMPLEMENTED**,
> **SCAFFOLDED**, **PLANNED**. The IA is the target; current code
> (`apps/web/app`) uses flat per-world routes and is mid-migration to the tree
> below. Where the two disagree, this document is the destination.

---

## 1. First principles for the IA

1. **Place before prompt.** You are always *somewhere* — Home, a world, an
   object. There is no global "blank box." The blank box is a failure state we
   design against.
2. **Worlds are the primary axis.** The IA is not organized by feature
   (chat / files / settings) but by *domain* (Coding / Healthcare / Legal).
   Features are modes *inside* a world.
3. **Objects are addressable and durable.** A Conversation, an Artifact, an
   Agent run — each has a stable identity, a URL, a provenance trail, and a
   home. Nothing important lives only in scroll-back.
4. **Global destinations are cross-world lenses.** Memory, Agents, Artifacts,
   Tasks, Knowledge are *views across worlds*, scoped and filtered by world,
   never a second navigation hierarchy that competes with worlds.
5. **Safety is structural, not decorative.** Boundaries in the IA (world
   isolation, Vault scoping, approval gates) are the same boundaries the
   runtime enforces. The map does not promise more than the engine keeps.

---

## 2. Top-level IA map

Indented outline of the entire product. Each leaf is a navigable destination;
each `[param]` is a dynamic segment.

```
Ronin AI OS
│
├── Home  (/)
│     • personalized entry surface — greeting, calendar-of-work,
│       recent artifacts, world shortcuts, suggested next actions
│     • explicitly NOT a chat box
│
├── Worlds  (/worlds)
│     • directory of Industry Worlds; health + enablement state
│     └── World  (/worlds/[world])
│           ├── World Home            — overview, pinned objects, world status
│           ├── Chat                  (/worlds/[world]/c/[conversation])
│           ├── Canvas                (/worlds/[world]/canvas/[doc])
│           ├── Artifacts             (/worlds/[world]/artifacts)
│           ├── Agents                (/worlds/[world]/agents)
│           ├── Actions               — world-local tool/action catalog
│           ├── Knowledge             (/worlds/[world]/knowledge)
│           └── Visualization         — world-local dashboards/views
│
├── Command Center  (CMD+K)                     ← overlay, not a route
│     • navigate / act / ask / run-agent
│
├── Memory  (/memory)
│     • cross-world lens onto Vault; scoped, visible, revocable
│     └── Memory item  (/memory/[item])
│
├── Agents  (/agents)
│     • agent directory + run history across worlds
│     └── Agent run  (/agents/[run])
│
├── Artifacts  (/artifacts)
│     • cross-world artifact library
│     └── Artifact  (/artifacts/[artifact])
│
├── Knowledge  (/knowledge)
│     • connected + local knowledge sources; indexing status
│     └── Source  (/knowledge/[source])
│
├── Vault  (/vault)                              ← IMPLEMENTED
│     • the substrate: scoped memory + isolation boundaries, auditable
│
├── Forge  (/forge)                              ← IMPLEMENTED
│     • provenance, redaction, training bundles, job states
│     └── Job  (/forge/[job])
│
├── Tasks  (/tasks)                              ← IMPLEMENTED (scaffold)
│     • scheduled + background work; the calendar-of-work backend
│
├── Settings  (/settings)
│     ├── Providers & Models   — provider-agnostic registry, local-first
│     ├── Autonomy & Approvals — autonomy levels, approval-gate policy
│     ├── Privacy & Memory     — retention, training-eligibility consent
│     ├── Appearance           — Sumi theme, density, motion
│     └── Worlds               — enable/disable packs, health
│
└── Account  (/account)
      • identity, devices, sessions, export/delete
```

### 2.1 Node purpose table

| Node | Route | Kind | Purpose | Status |
|---|---|---|---|---|
| Home | `/` | Surface | Personalized entry: greeting, calendar-of-work, recent artifacts, world shortcuts, suggested actions. Never a chat box. | PLANNED |
| Worlds | `/worlds` | Directory | Browse/enter Industry Worlds; shows health and enablement per pack. | IMPLEMENTED |
| World | `/worlds/[world]` | Container | A domain workspace scoping tools, memory, safety, evals. Hosts all interaction modes. | IMPLEMENTED (flat routes) |
| Chat | `/worlds/[world]/c/[id]` | Object/Surface | A conversation inside a world. | SCAFFOLDED |
| Canvas | `/worlds/[world]/canvas/[id]` | Object/Surface | Freeform spatial/document surface. | PLANNED |
| Artifacts (world) | `/worlds/[world]/artifacts` | Lens | World-local artifact list. | SCAFFOLDED |
| Agents (world) | `/worlds/[world]/agents` | Lens | World-local agents + runs. | SCAFFOLDED |
| Actions | — (in-world) | Catalog | World-local tools/actions the pack exposes. | SCAFFOLDED |
| Knowledge (world) | `/worlds/[world]/knowledge` | Lens | Sources indexed for this world. | PLANNED |
| Visualization | — (in-world) | Surface | World-local dashboards and structured views. | PLANNED |
| Command Center | `CMD+K` | Overlay | Universal navigate/act/ask/run-agent entry point. | PLANNED |
| Memory | `/memory` | Lens | Cross-world view of Vault; visible + revocable. | PLANNED |
| Agents | `/agents` | Lens | Cross-world agent directory + run history. | SCAFFOLDED |
| Artifacts | `/artifacts` | Lens | Cross-world artifact library. | IMPLEMENTED |
| Knowledge | `/knowledge` | Lens | All connected/local sources + index health. | PLANNED |
| Vault | `/vault` | System | Scoped-memory substrate; isolation + audit. | IMPLEMENTED |
| Forge | `/forge` | System | Provenance, redaction, bundles, training job states. | IMPLEMENTED |
| Tasks | `/tasks` | System | Scheduled/background work; backs calendar-of-work. | IMPLEMENTED (scaffold) |
| Settings | `/settings` | System | Providers, autonomy, privacy, appearance, worlds. | PLANNED |
| Account | `/account` | System | Identity, devices, sessions, export/delete. | PLANNED |

**Lens** = a filtered read-across of objects that live inside worlds.
**System** = infrastructure surface that spans all worlds.
**Overlay** = renders above the current place without changing it.

---

## 3. The Worlds model

A **World** is the runtime instantiation of an *industry pack*
(`industry-packs/<id>/manifest.yaml`). It is the unit of scope for four things.

### 3.1 What a world scopes

| Dimension | What the world defines | Enforcement |
|---|---|---|
| **Tools / Actions** | Which tools, adapters, and actions are mounted (e.g. Coding gets repo/test/build tools; Healthcare Information gets citation + guideline retrieval, and *no* diagnosis/prescription/dispatch tools). | Pack manifest allow-list; unlisted tools are not mounted. |
| **Memory** | A memory scope (Vault namespace) that other worlds cannot read. | Vault isolation — healthcare memory cannot surface in coding, and this is tested. |
| **Safety** | Domain safety rules, fail-closed blocks, required approval gates, autonomy ceiling. | Pack safety suite; a pack cannot reach `stable` unless its safety/privacy/grounding suites pass. |
| **Evaluations** | The eval suites that gate the pack's own quality and promotion. | Eval gate (`packages/industry-sdk/.../eval_gate.py`); fail = pack does not promote. |

### 3.2 Fail-closed entry

A world **fails closed**:

- If its manifest does not validate, it does **not load**.
- If it is disabled or unhealthy, it **cannot be entered** — the Worlds
  directory shows it, greyed, with the reason (`DISABLED`, `UNHEALTHY`,
  `EVAL_FAILING`), not a dead link.
- Current state: 3 worlds enabled, 17 disabled futures visible as
  "coming" tiles. Disabled worlds are part of the IA (they occupy a slot and a
  route) but are not enterable.

### 3.3 Switching worlds — what changes vs. what persists

Switching worlds is a **context switch**, not a page reload of the same app.

**Changes on switch:**
- Left-rail active world highlight and the world's accent framing.
- The set of mounted tools/actions and the world-local mode set.
- The **memory scope** — the right panel's Memory section now reflects the new
  world's Vault namespace only.
- The safety posture and autonomy ceiling shown in the right panel.
- Suggested actions and Command Center's default action set.

**Persists across switch:**
- Your identity, providers/model preferences, device, theme.
- The global lenses (Memory, Agents, Artifacts, Knowledge) remain reachable,
  but re-scope to the new world by default (with an "all worlds" toggle).
- Any running agent or task keeps running; it is surfaced under its owning
  world and in the global Agents lens.

### 3.4 Shared vs. world-local resources

| Resource | World-local | Shared (global) |
|---|---|---|
| Conversations | ✔ owned by a world | — |
| Canvas / artifacts | authored in a world | surfaced in the global Artifacts lens |
| Memory items | scoped to world's Vault namespace | user-profile-level items (name, preferences) marked *global* |
| Agents | world-scoped tool access | agent *definitions* can be shared; runs are world-tagged |
| Knowledge sources | attached per world | a source can be *linked* to multiple worlds (indexed per scope) |
| Providers / Models | — | registry is global; a world may pin a default/allowed set |
| Approvals | raised in world context | audit trail is global (`/vault` audit) |

Rule: **objects are born in a world; lenses read across worlds.** A resource is
never silently promoted from world-local to global — sharing is an explicit,
audited action (this is the same honesty commitment that governs
training-eligibility).

---

## 4. Object model

The core nouns of Ronin and how they relate. These are the addressable things
the IA is built to hold.

### 4.1 The nouns

| Object | One-line definition | Home |
|---|---|---|
| **World** | A scoped domain workspace instantiated from an industry pack. | `/worlds/[world]` |
| **Conversation** | An ordered exchange (chat mode) within a world. | in a world |
| **Artifact** | A durable output — document, canvas, code file, dataset, report. | authored in a world; listed globally |
| **Agent** | A configured autonomous/semi-autonomous worker with a tool scope and autonomy level. | world-scoped, globally listed |
| **Task** | A unit of scheduled or background work (may be produced by an agent). | `/tasks` |
| **Memory item** | A single visible, revocable remembered fact/preference/context. | `/vault`, viewed via `/memory` |
| **Knowledge source** | A connected or local corpus indexed for retrieval. | `/knowledge` |
| **Approval** | A pending human decision required before a gated action proceeds. | right panel; audited in Vault |
| **Model / Provider** | An inference backend (local or remote) and the provider that serves it. | `/settings` registry |

### 4.2 Relationships

```
User
 └─ owns ─▶ World (many)
              ├─ contains ─▶ Conversation ─▶ produces ─▶ Artifact
              ├─ contains ─▶ Agent ─▶ launches ─▶ Task ─▶ produces ─▶ Artifact
              ├─ scopes  ─▶ Memory item        (Vault namespace = World)
              ├─ links   ─▶ Knowledge source   (indexed per World scope)
              └─ mounts  ─▶ Action/Tool  (from pack manifest)

Agent ─ requests ─▶ Approval ─ gates ─▶ Action        (fail-closed)
Conversation / Agent ─ uses ─▶ Model  (via Provider, from global registry)
Every action, approval, memory write ─ appends ─▶ Audit entry (Vault)
Artifact / Task / Memory item ─ carries ─▶ Provenance (source, model, inputs)
```

Key invariants:

- **Provenance is mandatory.** Every Artifact, Task output, and Memory write
  records what produced it (world, model/provider, inputs, and whether tools or
  retrieval were used). This backs the right-panel Provenance section and the
  honesty labels.
- **Approvals gate, they don't advise.** An Approval sits *between* an Agent's
  intent and a gated Action. Denied or expired = the Action does not run
  (fail-closed).
- **Memory is scoped by World.** A Memory item belongs to exactly one Vault
  namespace (a world, or the explicit global/profile namespace). Cross-world
  reads are blocked, not merely discouraged.
- **Models are global, choices are local.** The Provider/Model registry is
  shared; a world constrains which are allowed and which is default.

---

## 5. Route / URL architecture (Next.js App Router)

Routes live under `apps/web/app`. Global destinations are top-level segments;
worlds nest their modes and objects.

| Path | Segment file | Renders | Notes |
|---|---|---|---|
| `/` | `app/page.tsx` | Home | Personalized entry surface. |
| `/worlds` | `app/worlds/page.tsx` | Worlds directory | Health + enablement per pack. |
| `/worlds/[world]` | `app/worlds/[world]/page.tsx` | World Home | Redirects to last mode or overview. |
| `/worlds/[world]/c/[id]` | `.../c/[id]/page.tsx` | Conversation | `c` short segment for chat. |
| `/worlds/[world]/canvas/[id]` | `.../canvas/[id]/page.tsx` | Canvas doc | |
| `/worlds/[world]/artifacts` | `.../artifacts/page.tsx` | World artifacts | Deep-links to global artifact view, world-filtered. |
| `/worlds/[world]/agents` | `.../agents/page.tsx` | World agents | |
| `/worlds/[world]/knowledge` | `.../knowledge/page.tsx` | World knowledge | |
| `/artifacts` | `app/artifacts/page.tsx` | Artifact library | Cross-world lens. IMPLEMENTED. |
| `/artifacts/[id]` | `app/artifacts/[id]/page.tsx` | Single artifact | |
| `/agents` | `app/agents/page.tsx` | Agent directory | |
| `/agents/[run]` | `app/agents/[run]/page.tsx` | Agent run detail | |
| `/memory` | `app/memory/page.tsx` | Memory lens | Read-across of Vault. |
| `/memory/[item]` | `app/memory/[item]/page.tsx` | Memory item | Revoke/edit here. |
| `/knowledge` | `app/knowledge/page.tsx` | Knowledge sources | |
| `/vault` | `app/vault/page.tsx` | Vault | IMPLEMENTED. |
| `/forge` | `app/forge/page.tsx` | Forge | IMPLEMENTED. |
| `/forge/[job]` | `app/forge/[job]/page.tsx` | Forge job | Job states + provenance. |
| `/tasks` | `app/tasks/page.tsx` | Tasks | IMPLEMENTED (scaffold). |
| `/settings` | `app/settings/page.tsx` | Settings root | Tabbed sub-sections below. |
| `/settings/providers` | `.../providers/page.tsx` | Providers & Models | |
| `/settings/autonomy` | `.../autonomy/page.tsx` | Autonomy & Approvals | |
| `/settings/privacy` | `.../privacy/page.tsx` | Privacy & Memory | |
| `/settings/appearance` | `.../appearance/page.tsx` | Appearance (Sumi) | |
| `/settings/worlds` | `.../worlds/page.tsx` | Enable/disable packs | |
| `/account` | `app/account/page.tsx` | Account | |
| `/signin` | `app/signin/page.tsx` | Auth | IMPLEMENTED. |

### 5.1 Route conventions

- **Worlds are the only deep hierarchy.** Everything else is one or two levels.
  We resist a fifth level of nesting; deeper context belongs in the right panel
  or query params, not the path.
- **Migration note.** Current code exposes flat per-world routes (`/coding`,
  `/education`, `/healthcare`) plus `/dashboard`, `/research`. The target folds
  these under `/worlds/[world]`; flat paths become permanent redirects. This is
  a deliberate, labeled migration — not a claim that `/worlds/[world]` is fully
  live today.
- **Query params carry ephemeral state**, not identity: `?panel=memory`,
  `?mode=canvas`, `?q=...` for Command Center pre-fill, `?scope=all` to widen a
  lens beyond the current world.
- **Command Center is never a route.** It is an overlay (`CMD+K`) that *emits*
  navigation to these routes.

---

## 6. Cross-cutting surfaces

Three surfaces overlay the IA rather than living inside it. They are available
from every place.

### 6.1 Command Center (CMD+K)

The universal entry point to **navigate, act, ask, and run agents**. It floats
above the current place, reads the current world as context, and closes back to
where you were. It is the fastest path to any node in Section 2. Detailed
behavior lives in `03-navigation-architecture.md`.

### 6.2 Right intelligence panel

A persistent, collapsible panel on the right of the workspace with five
sections: **Context**, **Memory**, **Provenance**, **Plan & Agents**,
**Approvals**. It is world-scoped (it reflects the active world's memory and
safety posture) and it **auto-opens on Approvals** when a gated action is
pending — the one case where the system interrupts you, because fail-closed
safety must be visible. Full spec in `03-navigation-architecture.md`.

### 6.3 Approvals

Approvals are both an object (Section 4) and a cross-cutting surface. A pending
approval:

- surfaces in the right panel (auto-opened) regardless of current place,
- blocks the specific gated action, not the whole app,
- records to the global audit trail whether approved, denied, or expired,
- honors the world's autonomy level (higher autonomy = fewer gates, never zero
  for destructive/irreversible actions).

---

## 7. Empty, offline, and first-run states (IA level)

The IA must be legible when there is *nothing yet* and when there is *no
network*. These are designed states, not fallbacks.

### 7.1 First run

- Land on **Home**, not a chat box. Home shows a warm greeting, the Worlds
  directory front-and-center, and one suggested first action per enabled world.
- No pre-created conversations. The "calendar-of-work" shows an empty, inviting
  state ("Nothing scheduled — pick a world to begin"), never a spinner or a
  fake sample.
- Memory, Agents, Artifacts lenses show honest empty states describing what
  *will* accumulate here and why it is private/local, not marketing copy.

### 7.2 Empty per-node states

| Node | Empty state |
|---|---|
| Worlds | Enabled worlds shown; disabled futures greyed with reason. Never hidden. |
| A world | "No conversations yet" + the world's suggested first actions + its safety summary. |
| Memory | "Ronin remembers nothing yet. What it learns will appear here — visible and revocable." |
| Artifacts | "Things you and Ronin make land here." + entry points into worlds. |
| Tasks | "No scheduled or background work." |
| Approvals | Absent from panel entirely when none pending (no empty stub). |

### 7.3 Offline / local-first

Ronin is **local-first**: the app is usable without network.

- Home, Worlds, Vault/Memory, Artifacts, Tasks, and any locally-served model
  remain fully functional offline.
- Remote-provider-dependent actions show an **honest status** — a world or
  action that requires a remote model when offline is labeled `BLOCKED_OFFLINE`
  and offers the local alternative if one exists, rather than failing silently.
- The provider registry marks each model local vs. remote; the IA never hides
  that a capability is unavailable — it shows *why* and *what still works*.
- Knowledge sources that are remote show a stale/last-synced marker offline;
  local sources keep working.

### 7.4 Degraded / unhealthy states

- An unhealthy world is visible but not enterable (`UNHEALTHY`), consistent with
  fail-closed entry (§3.2).
- A failing eval suite surfaces on the world tile and in `/settings/worlds`;
  the pack does not promote.
- These states use the honesty labels (`VERIFIED` / `IMPLEMENTED` /
  `BLOCKED_*`) rather than optimistic placeholders.

---

## 8. IA anti-patterns (things we explicitly refuse)

- **No blank-box home.** Home is a place, not a prompt.
- **No duplicate hierarchy.** Global lenses do not become a second nav tree
  competing with worlds; they are read-across views.
- **No silent cross-world leakage.** Memory and tools never cross world
  boundaries implicitly.
- **No optimistic status.** A generated bundle is never shown as trained; an
  offline-blocked action is never shown as available.
- **No orphan objects.** Every Conversation, Artifact, Agent run, and Memory
  item has a home, a URL, and provenance.

---

## 9. Cross-references

- Navigation, panes, keyboard model, Command Center behavior:
  `03-navigation-architecture.md`.
- Worlds as industry packs and fail-closed loading:
  `docs/product/ronin-ai-os-vision.md`, `industry-packs/<id>/manifest.yaml`.
- Vault isolation and audit: `packages/vault`.
- Provenance, redaction, training job states: `training/` (Forge).
- Design identity "Sumi" (washi, clay accent `#a98467`, negative space,
  near-silent motion): design system doc (`01-*`).
