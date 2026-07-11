# v1.0.0-rc.2 — Honest RC Baseline

Measured on the `hardening/rc-phase0` branch (commit `08110f1`), macOS / Python
3.14.4, **no provider API key configured**. This records only real, measured
results. Anything not run is marked **skipped / not-run with a reason**. No
live-provider coding score and no SWE-bench resolved-rate is claimed — Ronin
ships the eval *harness*, not a published score.

## Measured (run)

| Metric | Result |
|---|---|
| Full test suite | **3325 passed, 0 failed** in 48.9s (`uv run --frozen pytest` across all packages) |
| Per-package | agent-patterns 188 · eval-suite 45 · memory 11 · hardening 66 · mcp-servers 67 · cli 2847 · relay 45 (+ apps/demo, apps/api in total) |
| Affected packages (Phase 0 touched) | hardening 66 · relay 45 · cli gate/permission/approval subset 170 — all green |
| New tests added this phase | token-budget fail-closed +8 · relay traversal +9 · tool-gate drift +5 |
| Package build | **passed** — `uv build --all-packages` built sdist + wheel for every package |
| Startup | `ronin --version` **0.54s** (warm) |
| FakeProvider eval | **passed** — eval-suite's 45 tests run deterministically offline ($0). A correctness gate, not a quality score. |

## Not run / skipped (honest)

| Metric | Status | Reason |
|---|---|---|
| Install smoke (clean wheel install) | partial | `uv build` verified; full clean-venv wheel install deferred to Phase 2 (PyPI readiness). CI runs a `ronin init/tools/doctor/version` smoke. |
| Pipeline dry-run | not run | The dry-run exercises the planner (a model call); omitted from this offline $0 baseline. Exercised live in Phase 1. |
| Live-provider coding eval | skipped | No provider key in this environment. No `ronin eval` / `ronin bench` score against a real model. |
| SWE-bench resolved-rate | not run | No dataset + repo checkout provisioned; the local executor is Docker-less single-checkout. No resolved-rate claimed. |

## What this baseline is (and is not)

- **Is:** proof the suite is green, the packages build, the CLI starts fast, and
  the offline correctness gates hold — a real floor before final v1.0.
- **Is not:** a coding-quality benchmark or a head-to-head vs any other tool.
  Producing an honest, variance-caveated quality number requires a live model
  run (a config-resolved provider) and is tracked as a separate, approval-gated
  step (see the Stage A PR plan, PR-0.11).
