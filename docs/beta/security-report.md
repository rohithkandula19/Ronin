# Ronin AI OS — beta security report

Scope: the security posture of the controlled-beta control plane as implemented
and tested locally. Honest labels throughout. This is not a third-party
penetration test; a full browser/web red-team is BLOCKED_INFRASTRUCTURE (no
deployed surface) and a professional audit is out of scope for this
environment.

## Threat model (beta)

Assets: user accounts, org data (memory, documents), usage/billing records,
and — most importantly for cost — the ability to spend money via a provider.
Adversaries: a curious/abusive tester, a compromised tester account, and
accidental self-inflicted spend.

## Controls (VERIFIED locally = covered by passing tests)

| Control | Mechanism | Status |
|---|---|---|
| Spend containment | inference gateway is the single egress to paid providers; kill switch; 4-level quotas; idempotent ledger; unknown price never free | VERIFIED |
| Access control | `ronin_identity` fail-closed RBAC; org-scoped everything; cross-org denial tested | VERIFIED |
| Beta gating | `ronin_platform` invite-only default; access codes with use counts; domain allowlist | VERIFIED |
| Abuse throttling | `ronin_platform` token-bucket rate limiter, per-subject/endpoint | VERIFIED |
| Safety floor independence | security-sensitive feature flags barred at the store; floor never reads a flag | VERIFIED |
| Credential hygiene | no plaintext secrets stored; API keys are salted PBKDF2 hashes; verify() only | VERIFIED |
| Secret non-leakage | `ronin_observability` redaction filter on every log record/field; `ronin_support` redacts notification bodies; `ronin_storage.docproc` redacts extracted text | VERIFIED |
| Storage isolation | per-org namespacing; path-segment validation prevents traversal; checksum verify on read | VERIFIED |
| Env hardening | `check_environment` refuses default keys / debug / demo / `*` CORS / local prod DB / unlimited spend | VERIFIED |
| Fault containment | `ronin_jobs` retries with backoff then dead-letters; error taxonomy caps retries; user_message never leaks raw detail | VERIFIED |
| Data-consent | training eligibility requires explicit recorded consent; revocation drops it | VERIFIED |

## Secret management

- No secrets in source control. A repo-root `.gitguardian.yaml` scopes
  ignored paths to test dirs; test fixtures avoid credential-shaped literals
  (assembled at runtime).
- Provider keys / DB URLs are supplied only via environment at deploy time and
  validated by `check_environment`; the app refuses to start on unsafe prod
  config.

## Known gaps / blocked items (honest)

- **Web/browser red-team** — BLOCKED_INFRASTRUCTURE (no deployed frontend/
  backend surface here). XSS/CSRF/DOM testing must run against a real deploy.
- **Third-party penetration test** — not performed; recommended before public
  stable.
- **Distributed rate limiting** — current limiter is in-process; a shared
  backend is needed once there is more than one instance (stage 3).
- **Real authn/session hardening at the edge** — depends on the deploy
  (TLS, cookie flags, CSRF tokens) — BLOCKED_INFRASTRUCTURE.
- **Live payment security (PCI, etc.)** — N/A while payments are disabled by
  design; revisit when authorized.
- **Legal/regulatory** — privacy/ToS are DRAFT_REQUIRES_LEGAL_REVIEW;
  healthcare regulatory posture is BLOCKED_REGULATORY.

## Recommendation

The **control plane** is safe for a local / trusted-tester beta. Do not expose
a public surface until the BLOCKED_INFRASTRUCTURE web red-team and edge
hardening are completed against a real deployment, and legal review of the
DRAFT documents is done.
