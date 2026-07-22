# Public-beta entry assessment

Classifies every item from the private-alpha final report
(`docs/audits/ronin-ai-os-private-alpha-final-report.md`) for public-beta
purposes. Evidence-based; blockers labeled honestly.

## Classification

| Alpha item | Class | Action taken this branch |
|---|---|---|
| Safety-floor seam (web can't bypass) | VERIFIED — carry forward | reused; no change needed |
| Vault isolation / consent | VERIFIED — carry forward | reused by workflows |
| Forge generated≠trained | VERIFIED — carry forward | — |
| No uncontrolled model spend | **Must fix before beta** | ✅ inference gateway: kill switch, multi-level quotas, ledger |
| Feature-flagged controlled rollout | **Must fix before beta** | ✅ ronin-platform flags (backend, audited) |
| Rate limiting / abuse controls | **Must fix before beta** | ✅ token-bucket limiter (dev in-memory) |
| Controlled beta access (not open reg) | **Must fix before beta** | ✅ invite/waitlist/code/domain modes, default invite-only |
| Fail-closed production env validation | **Must fix before beta** | ✅ envcheck refuses unsafe prod config |
| Interactive Coding agent over a provider | Requires external credentials | BLOCKED_CREDENTIALS — gateway ready; wire a key to enable |
| Web build / DOM e2e / screenshots | Requires paid/infra (npm/browser) | BLOCKED_INFRASTRUCTURE |
| `ronin-code-v1` fine-tune | Requires hardware | BLOCKED_HARDWARE — bundles GENERATED only |
| Live payments (Stripe) | Requires credentials + authorization | NOT ENABLED — billing architecture is design-stage; do not enable |
| Staging deployment | Requires credentials + authorization | BLOCKED_CREDENTIALS |
| PostgreSQL/object-storage prod backends | Requires infrastructure | BLOCKED_INFRASTRUCTURE — SQLite/local backends work; portable schema |
| Legal docs (Terms/Privacy/Beta) | Requires expert review | BLOCKED_EXPERT_REVIEW — drafts must be marked DRAFT_REQUIRES_LEGAL_REVIEW |
| Healthcare clinical validation | Regulatory | BLOCKED_REGULATORY — informational-only, training off |

## Repaired locally before continuing

The five "must fix before beta" cost/abuse/access controls above are
implemented and tested on this branch (33 new tests across
`ronin-inference` + `ronin-platform`). No growth systems were built on top of
broken controls.

## Explicitly NOT done (and why)

- No public deployment, no live payment provider, no GPU training, no uncapped
  paid resources — all gated on credentials/authorization/hardware not present.
  These are labeled, never faked.
