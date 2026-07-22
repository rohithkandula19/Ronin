# Ronin AI OS — architecture

## Layering (evolve, don't rewrite)

```
                 apps/web (Next.js)        CLI / TUI (packages/cli)
                       |                          |
                 apps/api  /api/v1  ──────────────┘  (both call the SAME runtime)
                       |
   ┌───────────────────┼───────────────────────────────────────────┐
   │                   │                                             │
Industry SDK      Ronin Core (packages/cli + agent-patterns)     Vault
(packages/         run_code_agent(gate_cb,on_step_cb,...)       (packages/
 industry-sdk)     approval gates · destructive floor ·          vault)
 packs · registries  offline/free · EmbeddedProvider · MCP
 · eval gate
   │
industry-packs/*   training/ (Forge: provenance·redaction·quality·bundles·jobs)
```

The rule from the master plan — *"there must not be separate intelligence
implementations for the CLI and web application"* — is honored: `apps/api`'s
v1 layer holds **no** model or agent logic. It serves worlds/models/adapters
from the Industry SDK and registries, and memory from the Vault. When the
Coding World's interactive runtime is wired (next stage), it will call the
existing `run_code_agent(...)` with an HTTP-driven `gate_cb`, so web approvals
and the destructive floor are the *same* code the terminal uses.

## New packages this foundation added

- `packages/industry-sdk` — manifest schema + discovery + registry (fail
  closed), model/adapter registries with lifecycle honesty gates, and the
  pack-owned evaluation gate.
- `packages/vault` — scoped memory with cross-industry isolation, consent-gated
  training eligibility, recall audit, retention.
- `apps/api/csk_api/v1` — additive HTTP surface (auth/worlds/workspaces/models/
  adapters/memory/audit) over the above.
- `training/` (extended) — Forge: provenance, redaction, quality, bundles, jobs.

## Data model

Legacy briefing tables are untouched. v1 adds portable `aios_*` tables
(`aios_users`, `aios_sessions`, `aios_workspaces`, `aios_world_entries`,
`aios_audit_events`) created by `Base.metadata.create_all`. Registry and vault
state are local JSON stores behind swappable interfaces (a database can back
them later without changing callers). Alembic migrations are the documented
production step; the schema uses only portable column types so the move to
PostgreSQL is mechanical.

## Request flow (target, per the master plan)

`request → workspace context → industry pack → policy pre-check → capability
requirements → base model → adapter → retrieval → tool planning → agent
execution → policy post-check → citation verification → artifact → audit →
response`. Today the foundation implements the pack activation, capability/
model/adapter selection, memory routing with isolation, and audit legs; the
agent-execution leg reuses Ronin Core; retrieval/citations/artifacts are the
next stage.

## Ports the target tree will fill incrementally

The master plan's `packages/{core,agent-runtime,...}` and `services/*` layout
is a target. Rather than a destructive move, existing tested packages keep
their import paths; new capabilities land as new packages (as above) and are
re-exported/renamed only with a migration + compatibility layer + tests.
