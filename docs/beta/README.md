# Ronin AI OS — beta documentation

Operational documentation for the controlled beta. All status labels are honest
(VERIFIED / IMPLEMENTED / BLOCKED_* / DRAFT_*); nothing here asserts a
deployment, trained model, or live payment that does not exist.

> **The platform packages described here no longer exist.** `ronin-identity`,
> `ronin-jobs`, `ronin-observability`, `ronin-platform`, `ronin-support`,
> `ronin-tasks` and `ronin-research` were removed from the repository during the
> consolidation onto a single `ronin` binary. Each had **zero importers** — no
> shipped surface ever reached them — so nothing that runs today lost a
> capability. The runbooks below still describe how such a control plane *would*
> be operated, and are kept as design intent; every module name in them is a
> reference to code that has been deleted, not to something you can inspect.

| Doc | What it covers |
|---|---|
| [rollout-plan.md](rollout-plan.md) | Staged rollout (local → trusted testers → ≤100 → ≤1,000 → paid → public), entry gates, flag-driven cohorts, rollback |
| [slo.md](slo.md) | Service level objectives + error-budget policy (computed by `ronin_observability`) |
| [first-100-operations.md](first-100-operations.md) | Concrete runbook for operating the first 100 users; prerequisites, onboarding, daily ops, incident path, cost guardrails |
| [privacy.md](privacy.md) | Data handling as enforced in code (consent-gated training, isolation, redaction) |
| [security-report.md](security-report.md) | Beta security posture: controls VERIFIED locally + honest blocked gaps |
| [legal-terms-DRAFT.md](legal-terms-DRAFT.md) | **DRAFT_REQUIRES_LEGAL_REVIEW** — engineering-authored ToS/privacy starting point for counsel |

Final engineering report: [../audits/ronin-ai-os-public-beta-phase2-report.md](../audits/ronin-ai-os-public-beta-phase2-report.md).
