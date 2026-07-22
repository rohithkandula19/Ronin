# Ronin AI OS — operating the first 100 users

A concrete runbook for stage 2 (private beta, ≤100 users). Assumes an
authorized operator has supplied the credentials/backends that are currently
BLOCKED_CREDENTIALS / BLOCKED_INFRASTRUCTURE. Nothing here runs by itself.

## Prerequisites (all must be VERIFIED before onboarding user #1)

- [ ] `check_environment("production", cfg)` returns **no errors** for the
      target env (no default keys, no debug/demo, no `*` CORS, PostgreSQL DB,
      spend ceiling set). See `ronin_platform.envcheck`.
- [ ] One provider key wired to the inference gateway; per-request, per-user-
      daily, per-org-monthly, and provider-daily quotas configured.
- [ ] Kill switch reachable by the on-call operator and tested.
- [ ] PostgreSQL + object-storage backends provisioned; storage per-org quotas
      set (`ronin_storage`).
- [ ] Real email provider wired to `ronin_support` notifications (until then
      `EmailChannel` refuses — BLOCKED_CREDENTIALS).
- [ ] Backups + rollback verified on staging deploy.
- [ ] On-call rota and incident severities agreed (`ronin_support.incidents`).

## Onboarding a user

1. Add the invite (`ronin_platform` beta access, default invite-only) or issue
   an access code with a use count.
2. Create the org + owner membership (`ronin_identity`); set the plan slug and
   seat limit.
3. Enable the beta feature flag for that org/cohort (`ronin_platform.flags`);
   start at a small percentage and widen.
4. Confirm entitlements/quotas match the plan (`ronin_billing.entitlements`).

## Daily operations

- **Spend check**: read the gateway usage ledger; confirm no quota is being
  repeatedly hit (a sign to raise limits deliberately or investigate abuse).
- **SLO check**: review error budgets (`ronin_observability.slo`). At 50%
  consumed for a service, freeze that service's rollouts; at 90%, open an
  incident.
- **Job health**: inspect the dead-letter queue (`ronin_jobs`); requeue after
  fixing root cause. A growing DLQ is a release smell.
- **Support queue**: triage `ronin_support` tickets by priority; drive to
  resolved/closed.

## Incident path

1. Open an incident with a severity (`ronin_support.incidents`).
2. Post updates as status moves investigating → identified → monitoring →
   resolved; the status page derives from open incidents automatically.
3. Compute time-to-resolve; attach a postmortem (summary, impact, root_cause,
   action_items) — the model refuses a postmortem until the incident is
   resolved.
4. Feed action items back as `ronin_jobs` tasks or backlog.

## Cost guardrails (the program's hard rule)

- The gateway is the **only** place spend can happen; there is no other network
  egress to a paid provider in the codebase.
- Unknown provider prices are **never** treated as free — they raise, both in
  the gateway and in billing overage computation.
- If spend looks wrong for any reason, hit the kill switch first, diagnose
  second. Data and billing stay usable with inference killed.

## Escalation to stage 3 (≤1,000)

Only after: load tests pass against the real backends, distributed rate-limit +
worker backends are in place, and the incident runbook has been exercised at
least once in a game-day.
