# Ronin AI OS — controlled beta rollout plan

Status labels follow the program scheme (VERIFIED / IMPLEMENTED /
BLOCKED_CREDENTIALS / BLOCKED_INFRASTRUCTURE / BLOCKED_HARDWARE /
BLOCKED_EXPERT_REVIEW / NOT_IMPLEMENTED). This document is the staged plan;
it does **not** assert that any public deployment has happened. No public
deployment occurs without explicit credentials and authorization.

## Posture today

- **Local / trusted-tester posture: BETA_READY_WITH_LIMITATIONS.**
- **Public-facing launch: BLOCKED_CREDENTIALS + BLOCKED_INFRASTRUCTURE** until
  deploy credentials, a provider key, and a PostgreSQL / object-storage backend
  are supplied by an authorized operator.

The controls that make a controlled beta safe are implemented and tested
locally: the inference gateway (single spend choke point, kill switch, 4-level
quotas, idempotent ledger), feature flags (security-sensitive flags barred),
rate limiting, beta access modes (default invite-only), fail-closed env
validation, org/RBAC, storage quotas + isolation, durable jobs with
dead-lettering, observability with secret-redacting logs + SLOs, billing
metering with **live payments disabled by design**, and support/incident/
notification plumbing (external channels disabled).

## Stages and gates

| Stage | Population | Entry gate (must be true) | Spend posture |
|---|---|---|---|
| 0. Local / self | maintainers only | full suite green; kill switch VERIFIED | $0 (mock provider) |
| 1. Trusted testers | invite-only cohort | one provider key wired to gateway; per-user daily + org monthly quotas set; env validation passes for target env | capped by gateway quotas; provider daily budget enforced |
| 2. Private beta (≤100) | invite / access-code | PostgreSQL + object storage backends; real email provider; staging deploy with spend limits + backups + rollback; on-call rota | quota + provider budget; monthly platform ceiling enforced by envcheck |
| 3. Open beta (≤1,000) | waitlist drain | load tests pass against real backends; distributed rate-limit + worker backend; incident runbook exercised | as above, scaled ceilings |
| 4. Paid plans | opted-in orgs | **live payment provider + explicit authorization** (currently BLOCKED_CREDENTIALS; payments disabled by design) | metered billing; overage pricing table complete |
| 5. Public stable | general | full browser security red-team; legal review of DRAFT docs; healthcare regulatory review | — |

**No stage advances on optimism.** Each gate is a checklist of VERIFIED
conditions; a BLOCKED condition holds the stage.

## Flag-driven cohort control

Exposure is gated by `ronin_platform` feature flags (percentage rollout +
user/org/plan targeting), never by anything that touches the safety floor
(security-sensitive flags are barred at the store level). Roll a feature to a
named cohort first, watch SLOs and error budget (`ronin_observability`), then
widen the percentage.

## Rollback

- **Spend runaway** → gateway kill switch (data/billing stay usable), then
  investigate the ledger.
- **Bad release** → flip the feature flag off for the cohort; redeploy prior
  build (staging deploy is BLOCKED_CREDENTIALS until an operator wires it).
- **Data incident** → follow `first-100-operations.md` incident path; open a
  `ronin_support` incident, drive to resolved, attach postmortem.

## What this plan does NOT claim

- No model has been trained (ronin-code-v1 bundle is GENERATED; training is
  BLOCKED_HARDWARE — no GPU/MLX in this environment).
- No deployment, no live payments, no backups exist yet — all are gated.
- Legal/regulatory documents are DRAFT_REQUIRES_LEGAL_REVIEW.
