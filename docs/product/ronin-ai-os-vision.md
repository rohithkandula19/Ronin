# Ronin AI OS — product vision

Ronin AI OS turns Ronin from a terminal-native coding agent into a local-first,
provider-agnostic, multi-industry AI operating system — **without replacing the
runtime that already works.** The existing agent (approval gates, destructive
safety floor, offline/free modes, local inference) *is* Ronin Core; every new
surface calls it rather than reimplementing intelligence.

## The core idea: worlds, not a blank box

Instead of one empty prompt, a user enters a **world** — Coding, Education,
Healthcare Information — and gets a workspace with that domain's roles, tools,
safety rules, memory boundaries, adapters, and evaluations. A world is a
declarative **industry pack** (`industry-packs/<id>/manifest.yaml`) that
**fails closed**: if it doesn't validate, it doesn't load; if it's disabled or
unhealthy, it can't be entered.

## What shipped in this foundation

| Product | State | Where |
|---|---|---|
| Industry Pack SDK | IMPLEMENTED | `packages/industry-sdk` |
| Worlds (3 enabled + 17 disabled futures) | IMPLEMENTED | `industry-packs/` |
| Model + Adapter Registry | IMPLEMENTED | `packages/industry-sdk/.../model_registry.py` |
| Vault (scoped memory, isolation) | IMPLEMENTED | `packages/vault` |
| Evaluation gate (pack-owned suites) | IMPLEMENTED | `packages/industry-sdk/.../eval_gate.py` |
| Forge (provenance, redaction, bundles, job states) | IMPLEMENTED | `training/` |
| API v1 (auth, worlds, registries, memory, audit) | IMPLEMENTED | `apps/api/csk_api/v1` |
| Web World Navigator + rebrand + tests | IMPLEMENTED | `apps/web` |
| Ronin Code / Research / Developer Platform UIs | SCAFFOLDED / NOT_IMPLEMENTED | future |

## Honesty commitments (enforced in code, not just stated)

- A **generated** training bundle is never reported as a **trained** model
  (Forge job states + adapter lifecycle both forbid the jump).
- Nothing becomes **training-eligible** by default; it requires an explicit
  consent reference, and health/minor/credential data can never be eligible.
- **Healthcare** blocks autonomous diagnosis, prescription changes, and
  emergency dispatch, and cannot reach `stable` unless its safety, privacy,
  and grounding suites pass.
- Cross-industry memory **isolation** is enforced and tested (healthcare
  memory cannot surface in coding).
- Unknown model prices stay `null`, never a guessed number.

## What we do not claim

No guaranteed medical accuracy, no clinical validation, no AGI, no zero
hallucinations, no production deployment. Fine-tuning is for behavior, format,
tool-selection, and safety patterns — current facts come from retrieval and
tools.
