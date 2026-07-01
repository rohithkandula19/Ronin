# ronin — v1.0 Security & Safety Release Review

**Version:** 1.0.0-rc.1 · **Reviewed:** 2026-07-01 · **Method:** read-only review with file:line citations.

## Summary — zero release-blocking findings

All 12 checks **PASS**. No live secrets, the approval gate is fail-closed and default-deny, payments are unconditionally BLOCK in the outward-action path, offline mode is airtight, and every commit / PR / checkpoint-restore is explicitly confirmed.

| # | Check | Status | Evidence |
|---|---|---|---|
| 1 | No committed secrets | ✅ PASS | Full-repo scan: only synthetic fixtures match (`sk-ant-FAKEKEYNOTREAL…` in `packages/hardening/tests/test_secret_scanner.py`, AWS docs' `AKIAIOSFODNN7EXAMPLE`, self-labeled `# ronin:allow-secret`). Strict OpenAI/GitHub/Slack patterns: 0 real hits. |
| 2 | Fixtures clearly fake | ✅ PASS | Strings literally spell `FAKEKEYNOTREAL` / `FAKETESTKEY` / the AWS `EXAMPLE` key and carry `# ronin:allow-secret` markers. |
| 3 | Keys stored locally only; no phone-home | ✅ PASS | Config at `.ronin/config.toml` / `~/.config/ronin/config.toml` (`config.py:17-23`); `key_for()` resolves per-provider store → legacy field → env var only (`config.py:230-243`). No telemetry/analytics/posthog/mixpanel/sentry in `packages/cli/src`. Outbound calls are user-invoked features only. |
| 4 | Approval gate default-deny; BLOCK never auto | ✅ PASS | `approve()` (`approvals.py:357-394`) only skips the prompt for `AUTO and auto_ok`; BLOCK always asks; `_ask_yes_no` is default-deny (empty/EOF/error → False). `gate_level` sends payment/destructive → BLOCK before any auto path. |
| 5 | yolo / full-access loud warning | ✅ PASS | `--full-access`/`--god-mode` prints `⚠ FULL-ACCESS MODE — filesystem-wide, auto-approving every edit & command, no sandbox…` (`main.py:175-177`) + a persistent yellow `· auto-approve (YOLO)` session badge. |
| 6 | Offline strips network tools | ✅ PASS | `NETWORK_TOOLS` (`offline.py:19-24`) covers web_search/fetch_url/image/browse; `apply_offline` forces a local brain + clears failover; `strip_network_tools` filters them out (wired at `code_mode.py:682-684`). |
| 7 | No automatic payment path | ✅ PASS | `PAYMENT_MARKERS` + positive cost + `kind=="payment"` → BLOCK (`approvals.py`); outward-action executor gates **every** step through `approvals.approve` (`act.py:407-451`). |
| 8 | No destructive command without approval | ✅ PASS* | `run_command` ∈ `SENSITIVE_TOOLS` (`code_tools.py:23`); `DESTRUCTIVE_MARKERS` (`rm -rf`, `git push --force`, `drop table`, `mkfs`, fork-bomb) → BLOCK. Gated via `_selective_gate`; deny-rules are a kill-switch even under yolo. *See Boundary 1. |
| 9 | Pipeline commit/PR gated | ✅ PASS | `commit_gate` (`pipeline_finish.py:19-43`) requires a passing verdict (else explicit `--force`/confirm); `_do_commit`/`_do_pr` each call `_confirm` and abort on no; `--dry-run` makes zero git changes. |
| 10 | Read-only roles stay read-only | ✅ PASS* | researcher/reviewer/architect/verifier `read_only=True` (`roles.py`); `code_mode.py:627-637` filters to `_READONLY_CODE_TOOLS`; write/media tools guarded by `not read_only`. *See Boundary 2. |
| 11 | Untracked-diff capture read-only | ✅ PASS | `git diff --no-index` (`pipeline_diff.py:89-91`); no `git add`/write anywhere in the module; binary/oversized → metadata only; never raises. |
| 12 | Checkpoint restore gated | ✅ PASS | `--resume` restore target is never automatic; `Confirm.ask(..., default=False)`; decline exits unless `--force-resume`; tool-level `rewind` ∈ SENSITIVE_TOOLS. |

## Documented boundaries (not blockers — known, intentional, disclosed)

1. **`--yolo` / `--god-mode` scope — RESOLVED.** Previously the code-mode `run_command` gate auto-approved any command under god-mode. A **destructive floor** now sits in `_selective_gate` (`code_mode.py`) *before* the yolo short-circuit: a destructive `run_command` (via `is_destructive_command`) is never auto-approved in any mode — it shows a red block card and requires the user to type the phrase `run destructive` (default-deny; a headless run can never confirm). The `--god-mode` warning discloses the floor, and the chip strip pins `[DESTRUCTIVE FLOOR ACTIVE]`. Verified live: under `--god-mode`, `rm -rf /` is blocked while `ls -la` auto-approves. 9 tests in `test_destructive_floor.py`.

2. **Read-only roles are no-mutation, not no-network.** Researcher/reviewer/architect/verifier can't edit files, but `web_search`/`fetch_url` are available (read-only web reads). `--offline` strips them.

3. **Provider keys are stored in plaintext TOML** (`config.py:311-321`), local-only, not encrypted at rest — standard for a dev CLI. Keep your config dir private.

## Verdict

**Ship-safe.** No live secrets, fail-closed gating, hard-blocked payments, airtight offline mode, gated commit/PR/restore. The one substantive caveat (Boundary 1) is a documented property of an explicitly opt-in mode, disclosed here and in `docs/safety.md`.
