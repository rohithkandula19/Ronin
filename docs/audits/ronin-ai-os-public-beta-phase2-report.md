# Ronin AI OS — public beta, phase 2: platform packages

Branch `claude/ronin-last-check-6a1f43` (from `main` after #85–#90 merged).
Continues the public-beta program by building the remaining **locally-
achievable** platform layers. Honesty labels per the program scheme. Evidence =
code that ran here.

## What this phase adds (VERIFIED locally)

Six new self-contained, tested packages — evolving the platform, not rewriting
anything. All existing functionality (CLI, offline, safety floor, approval
gates, worlds/SDK, gateway, flags) is preserved; the full suite is green.

| Package | Responsibility | Tests |
|---|---|---|
| `ronin-identity` | Users, orgs, memberships, fail-closed RBAC, single-use invitations with seat limits, API keys stored as salted PBKDF2 hashes; cross-org denial | 37 |
| `ronin-storage` | Content-addressed blob store (memory + local-fs), per-org isolation + quotas + content-type allowlist + checksum verify; `docproc` stdlib-only extraction + deterministic chunking + redaction (binary formats honestly BLOCKED_DEPENDENCY) | 50 |
| `ronin-billing` | Plans, fail-closed entitlements, idempotent metering, subscription state machine, compute-only invoices (integer cents); **live payments disabled by design** (only `DisabledPaymentProvider` ships) | 45 |
| `ronin-jobs` | Deterministic durable queue/worker: idempotent enqueue, backoff retries → dead-letter → requeue; typed error taxonomy (retryable/severity), user_message never leaks raw detail | 52 |
| `ronin-observability` | Redacting structured logger, in-memory metrics + histograms + cardinality guard, caller-clock spans, SLO error-budget/burn-rate (zero-traffic → unknown), worst-wins health | 52 |
| `ronin-support` | Ticket + incident state machines, org-scoped ticket isolation, postmortem gated on resolution, status-page derivation; notifier redacts bodies, external channels disabled (BLOCKED_CREDENTIALS) | 53 |

**289 new tests**, all green.

## Design invariants held (each tested)

- **Fail-closed everywhere:** unknown role/permission/plan/limit/category →
  deny, never allow. Empty/zero-traffic SLO → `unknown`, never healthy.
- **No uncontrolled spend:** billing computes but never charges; the only money
  seam is `DisabledPaymentProvider`, which refuses. Unpriced meter overage
  raises (mirrors the gateway's "unknown price never free").
- **No secret/PII leakage:** observability logs, support notifications, and
  storage doc-processing all redact before anything is recorded/emitted.
  Credentials are stored only as salted hashes.
- **Org isolation:** identity, storage, and support all deny cross-org access
  with explicit tests.
- **Determinism:** every package takes caller-supplied `now`/ids — no
  wall-clock, no randomness, no network, reproducible.

## Test evidence

- `uv run --frozen pytest packages apps training -q` → **4,098 passed, 6
  skipped, 0 failed** (126s).
- `node --test apps/web/lib/*.test.mjs` → **8 passed**.
- `make verify-public-beta` runs backend + frontend + secret scan + all beta
  control/platform packages; staging/deploy steps intentionally excluded and
  labeled.

## Status of the broader program (unchanged, honest)

| Item | Status |
|---|---|
| ronin-code-v1 training | BLOCKED_HARDWARE (no GPU/MLX); reproducible bundle is GENERATED, not trained |
| Staging deploy / PostgreSQL / object storage | BLOCKED_CREDENTIALS + BLOCKED_INFRASTRUCTURE |
| Live payments | disabled by design (BLOCKED_CREDENTIALS + explicit authorization required) |
| Email/SMS/paging delivery | BLOCKED_CREDENTIALS (disabled channel stubs) |
| Web/browser security red-team | BLOCKED_INFRASTRUCTURE (no deployed surface) |
| Legal (ToS/privacy) | DRAFT_REQUIRES_LEGAL_REVIEW (`docs/beta/legal-terms-DRAFT.md`) |
| Healthcare regulatory | BLOCKED_REGULATORY |

## New docs

`docs/beta/`: `rollout-plan.md`, `slo.md`, `privacy.md`, `legal-terms-DRAFT.md`,
`first-100-operations.md`, `security-report.md`.

## Launch recommendation

Unchanged: **BETA_READY_WITH_LIMITATIONS** for a local / trusted-tester
posture. The control *and* platform layers a controlled beta needs now exist as
tested local packages. Public-facing launch remains BLOCKED_CREDENTIALS +
BLOCKED_INFRASTRUCTURE until an authorized operator supplies deploy
credentials, a provider key, and PostgreSQL/object-storage backends.
