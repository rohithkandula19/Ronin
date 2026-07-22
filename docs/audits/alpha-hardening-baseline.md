# Alpha hardening — baseline & reality audit

Branch `feat/ronin-ai-os-alpha-hardening`, from `feat/ronin-ai-os-foundation`
@ `8c92ee4`. Executable evidence only; prior completion reports were **not**
trusted — every claim below was re-checked by running code.

## Baseline metrics (measured)

| Metric | Value | How measured |
|---|---|---|
| Backend tests | 3,724 passed / 6 skipped / 0 failed | `uv run --frozen pytest packages apps training -q` (prior full run) |
| + Artifacts package | +8 passed | `pytest packages/artifacts` |
| Frontend tests | 8 passed | `node --test apps/web/lib/*.test.mjs` |
| TODO/FIXME in new source | 0 | `git grep -nE 'TODO|FIXME' packages/*/src apps/api/csk_api apps/web/{app,lib}` |
| `csk` in web app (excl. localStorage key) | 0 | `git grep csk apps/web/{app,lib}` |
| DB migrations | none (create_all only) | inspection — Alembic remains the documented prod step |

## Reality audit — verified vs. not (executable evidence)

| Claim | Verdict | Evidence |
|---|---|---|
| Industry SDK fails closed | VERIFIED | 24 tests incl. disabled/unhealthy/unsupported refusals |
| 3 worlds enabled, 17 disabled | VERIFIED | `test_repo_packs.py` asserts statuses |
| Adapter lifecycle forbids generated→released | VERIFIED | `test_model_registry.py` |
| Vault cross-industry isolation | VERIFIED | `test_vault_isolation.py` + through HTTP in `test_v1_api.py` |
| Eval gate refuses unsafe responder stability | VERIFIED | `test_eval_gate.py` |
| Forge separates generated from trained | VERIFIED | `test_forge_provenance.py` (`job.trained is False` for generated) |
| API v1 auth/isolation | VERIFIED | 9 tests (pbkdf2, no enumeration, cross-owner 404) |
| **Web action cannot bypass the destructive floor** | VERIFIED (this branch) | `test_v1_coding_safety.py` — imports the REAL `is_floored_tool_call` + `SENSITIVE_TOOLS`; `rm -rf /` and `git push --force` denied even when the user "approves" |
| Real destructive floor behaviour | VERIFIED | live: `is_floored_tool_call('run_command',{'command':'rm -rf /'})` → True; `ls` → False |
| Interactive model-driven Coding agent over a provider | BLOCKED_CREDENTIALS | needs a provider key; the safety *decision layer* is proven offline, the model loop is not run here |
| Web `next build` / DOM e2e / screenshots | BLOCKED_INFRASTRUCTURE | no `npm install` available; logic layer tested via `node --test` |
| `ronin-code-v1` fine-tune | BLOCKED_HARDWARE | no CUDA/Apple-Silicon GPU; bundles generate, training does not run |

## What this branch repairs/hardens (with regression tests)

1. **Safety seam (highest priority):** `apps/api/csk_api/v1/coding_runtime.py`
   composes the real runtime floor + sensitive-tools set + read-only-role
   check in the same order `code_mode`'s gate does, and exposes
   `build_gate_cb()` for `run_code_agent`. A front-end that approves everything
   still cannot execute a floored command. 8 invariant tests.
2. Artifacts store (structured, versioned) — committed on the foundation
   branch; carried here.

## Honest blockers carried forward

- Real remote/agent execution: BLOCKED_CREDENTIALS (no paid API on the core path by design).
- Web build/e2e/screenshots: BLOCKED_INFRASTRUCTURE (no npm install).
- Fine-tune training: BLOCKED_HARDWARE (no GPU) — bundles are GENERATED only.
