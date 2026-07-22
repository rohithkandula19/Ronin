# Ronin AI OS — service level objectives (beta)

These SLOs are the **targets** the beta operates against and the definitions
the `ronin_observability` SLO module computes error budgets from. They are
objectives, not measured production numbers — no production traffic exists yet,
so every "current" value is `unknown` until real traffic is fed to the SLO
tracker. Fail-closed: zero-traffic windows report `unknown`, never "healthy".

## Objectives

| Service | SLI | Objective (beta) | Window |
|---|---|---|---|
| API availability | successful non-5xx responses / total | 99.5% | 30d rolling |
| API latency | requests under 800ms server time / total | 95% | 30d rolling |
| Inference gateway | requests that resolve (served or cleanly refused, not errored) / total | 99.0% | 30d rolling |
| Job processing | jobs reaching a terminal non-dead-letter state within max_attempts / total | 98.0% | 7d rolling |
| Storage durability (local backend) | reads passing checksum / total reads | 99.99% | 30d rolling |

Objectives are deliberately modest for a controlled beta. They tighten before
open beta (stage 3) and again before public stable.

## Error budget

For objective `o` over window with `total` events and `good` events:

- success ratio = good / total (`unknown` if total == 0)
- budget consumed = (1 − ratio) / (1 − o)
- budget remaining = 1 − consumed (clamped ≥ 0)
- burn rate = observed failure rate / allowed failure rate

`ronin_observability.slo` computes these deterministically. Policy: at **50%**
budget consumed, freeze non-critical rollouts for that service; at **90%**,
freeze all rollouts and open an incident.

## Health & status

- `ronin_observability.health` aggregates component checks; **worst component
  wins** (down > degraded > ok).
- The public status page (stage 2+) derives overall status from open
  `ronin_support` incidents; no open incident → `operational`.

## Alerting

Alerting hooks are IMPLEMENTED as in-memory metrics + notification fan-out.
Real paging (email/SMS/PagerDuty) is BLOCKED_CREDENTIALS — the `EmailChannel`/
`SmsChannel` stubs refuse to send until an authorized operator supplies
provider credentials.
