# Ronin AI OS — public-beta readiness: final report

Branch `feat/ronin-ai-os-public-beta` (from `main` after #85 + #86 merged).
Honesty labels per the program's scheme. Evidence = code that ran here.

## Executive summary

This branch implements the **cost, abuse, access, and environment controls** a
controlled public beta requires — the program's most-emphasized "no
uncontrolled spending" mandate — as tested local packages, and classifies every
remaining item honestly. It does **not** deploy, enable live payments, train a
model, or claim production-readiness; those are credential/hardware/authorization
gated and labeled.

**Launch recommendation: `BETA_READY_WITH_LIMITATIONS`** for a *local / trusted-
tester* posture. Public-facing launch is **BLOCKED_CREDENTIALS +
BLOCKED_INFRASTRUCTURE** until deploy credentials, a provider key, and a
PostgreSQL/object-storage backend are supplied.

## What was built (VERIFIED locally)

- **Inference gateway** (`packages/inference`) — single choke point for spend:
  global kill switch (data/billing stay usable), multi-level cost quotas
  (request/user-daily/org-monthly/provider-daily) that refuse before any
  provider call, runaway max-token guard, local-only enforcement (no remote
  even on failover), failover that records usage once, append-only ledger
  idempotent on request id, unknown prices never shown as free, routing trace.
  10 tests.
- **Platform plumbing** (`packages/platform`) — feature flags (backend, audited,
  percentage rollout; security-sensitive flags barred), token-bucket rate
  limiter, beta access modes (default invite-only), fail-closed env validation.
  17 tests.

## Product status (labels)

| Product | Status |
|---|---|
| Ronin Core (runtime) | VERIFIED (preserved; full suite green) |
| Ronin Worlds / SDK | VERIFIED |
| Ronin Code — safety seam + cost controls | VERIFIED locally |
| Ronin Code — interactive agent over provider | BLOCKED_CREDENTIALS |
| Education / Healthcare workflows | IMPLEMENTED (deterministic, grounded) |
| Ronin Forge | IMPLEMENTED (bundles GENERATED; no training) |
| Vault / Research / Artifacts / Tasks | IMPLEMENTED |
| Billing (live payments) | NOT_IMPLEMENTED / disabled by design |
| Developer Platform | NOT_IMPLEMENTED (next stage) |

## Infrastructure status

| Area | Status |
|---|---|
| DB (SQLite local) | VERIFIED; PostgreSQL prod BLOCKED_INFRASTRUCTURE (portable schema ready) |
| Object storage | local backend design ready; S3 BLOCKED_INFRASTRUCTURE |
| Inference gateway | VERIFIED (mock provider) |
| Workers/scheduler | in-process VERIFIED (ronin-tasks); external BLOCKED_INFRASTRUCTURE |
| Model serving (GPU/vLLM) | BLOCKED_HARDWARE |
| Billing / notifications / monitoring exporters | SCAFFOLDED / design-stage |
| Staging deploy | BLOCKED_CREDENTIALS |

## Cost readiness

Kill switch VERIFIED; quotas at 4 levels VERIFIED; ledger idempotent VERIFIED;
unknown-price honesty VERIFIED. Provider budgets and platform monthly ceiling
are enforced by envcheck (prod refuses unlimited spend). Estimated beta costs:
$0 on the local/mock path; real cost depends on a provider key + traffic
(unverified — no live provider run here).

## Security & privacy

Carried-forward VERIFIED: web-cannot-bypass-floor, cross-owner/cross-industry
isolation, consent-gated training, no account enumeration. New: security-
sensitive feature flags barred; env validation blocks default keys/debug/demo/
'*' CORS/local-prod-DB. Full web red-team (XSS/CSRF DOM) remains
BLOCKED_INFRASTRUCTURE (no browser build). Legal docs BLOCKED_EXPERT_REVIEW.

## Test evidence

- `uv run --frozen pytest packages apps training -q` → **3,791 passed, 6 skipped, 0 failed**.
- `node --test apps/web/lib/*.test.mjs` → 8 passed.
- `make verify-public-beta` runs backend + frontend + secret scan + the beta
  control packages; staging/deploy steps intentionally excluded and labeled.

## Launch recommendation

**BETA_READY_WITH_LIMITATIONS** (local / trusted-tester). Not publicly launched.

## Exact next actions

- **Before trusted-tester rollout:** supply one provider key; wire the Coding
  agent loop to the gateway + `build_gate_cb`; enable via feature flag for a
  named cohort.
- **Before 100 users:** PostgreSQL + object storage backends; real email
  provider; staging deploy with spending limits + backups + rollback.
- **Before 1,000 users:** load tests (scaffolded targets only here — no
  scalability claim); distributed rate-limit + worker backends.
- **Before paid plans:** live payment provider + explicit authorization; the
  billing ledger/entitlements are design-stage only.
- **Before public stable:** full browser security red-team; legal review of the
  DRAFT_REQUIRES_LEGAL_REVIEW documents; regulatory review for Healthcare.
