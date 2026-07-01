# ronin — v1.0 Launch-Readiness Audit

**Version:** 0.59.0 · **Audited:** 2026-07-01 · **Method:** read-only static audit + live CLI checks.

This audit was produced by a read-only review of the repo (`pyproject.toml`, entrypoints, slash commands, README/docs claims) plus live `uv run ronin …` command checks. Blockers found during the audit have since been **fixed** (see the Status column).

## Summary

| # | Check | Status | Evidence / Notes |
|---|---|---|---|
| 1 | Package metadata correct | ✅ PASS | `pyproject.toml` name `ronin` 0.59.0, `requires-python>=3.11`, MIT; `packages/cli/pyproject.toml` name `ronin-cli` 0.59.0, full classifiers (Beta, 3.11–3.13) + `[project.urls]`. |
| 2 | Console scripts | ✅ PASS | `[project.scripts]`: `ronin` and `ro` → `ronin_cli.main:app`. |
| 3 | CLI entrypoints work | ✅ PASS | Live: `ronin --help`, `ronin version`, `ronin --version`, `ronin doctor`, `ronin pipeline --dry-run` all run with exit 0 and sane output. |
| 4 | `ronin version` / `--version` | ✅ PASS (fixed) | `ronin version` prints `ronin 0.59.0 (<sha>, <branch>)`. `ronin --version` **added** this wave (eager root flag). |
| 5 | `ronin doctor` | ✅ PASS | Config path, provider, model, base_url, provider-auth (honest present/missing), `--check` live provider ping, services, version. (Does not check Python version — see Follow-ups.) |
| 6 | `ronin update` | ✅ PASS | Git-checkout updater: fetch origin, refuse dirty tree without `--force`, reset to `origin/main`, `uv sync`. `--check` is non-mutating. Non-git installs handled with reinstall guidance. |
| 7 | `ronin code` works | ✅ PASS | `@app.command()` with help + docstring; coding agent, gated edits. |
| 8 | `ronin pipeline` works | ✅ PASS | Full role-handoff pipeline; `--dry-run` verified live. |
| 9 | `ronin --offline` | ✅ PASS | Root flag; `apply_offline` forces a local brain and strips network tools. |
| 10 | `/provider` `/free` `/theme` `/role` | ✅ PASS | All four registered in `SLASH_DISPATCH` and `SLASH_COMMANDS`. |
| 11 | `/help` accurate | ✅ PASS | All 37 `SLASH_COMMANDS` map to handlers (`voice` special-cased pre-dispatch). No orphans. |
| 12 | Docs match real commands | ✅ PASS (fixed) | README claims verified against code (see below). Stale test counts corrected to the measured number. |
| 13 | No stale "coming soon" as shipped | ✅ PASS | No vaporware claims; "TODO/FIXME" hits are the `guard` feature *detecting* them. |
| 14 | No fake benchmarks | ✅ PASS | No fabricated SWE-bench resolved-rate. `docs/BENCHMARK.md` presents a scoped, reproducible 6-task battery with an explicit honesty note on rate-limit confounds. SWE-bench refs describe the **harness capability**, not a claimed score. |
| 15 | No fake screenshots | ✅ PASS | Every README GIF/PNG reference resolves to a real file (`assets/ronin-demo.gif`, `assets/ronin-free.gif`, `docs/demo/ronin.gif`, `docs/demo/example-image.png`). |
| 16 | No secret leakage | ✅ PASS | Full-repo scan for `sk-ant-…`/`AKIA…`/`ghp_…`/private-keys found **only** test fixtures (`sk-ant-fake`, `sk-test-key`), scanner regexes, and doc placeholders (`AKIAIOSFODNN7EXAMPLE`). `.env.local` is gitignored + untracked. |
| 17 | Install script works | ✅ PASS (fixed) | **Was a FAIL** — `install.sh` was stale from the `csk→ronin` rename and installed a broken `csk` shim. **Fixed** this wave: installs `ronin`+`ro` shims, execs `uv run ronin`, corrected header + get-started hints. |

## README-claims cross-check (all verified against code)

- **"31-game arcade"** → `len(GAMES) == 31` ✅
- **Free on Gemini / Cerebras / Groq / OpenRouter / Ollama** → all present in `config.py` `PROVIDER_PRESETS` ✅
- **"37 slash commands"** → `len(SLASH_COMMANDS) == 37` ✅
- **Test count** → measured **3,274 passing** across all packages + apps; README badge/prose corrected from the stale 2475/2,232.

## Blockers found → resolved this wave

1. **`install.sh` installed a broken `csk` shim** (curl install non-functional). → **Fixed**: `ronin`+`ro` shims execing `uv run ronin`.
2. **README test counts stale/inconsistent** (badge 2475 vs prose 2,232 ×2, both wrong). → **Fixed**: corrected to the measured 3,274.

## Follow-ups (non-blocking, tracked for post-1.0)

- **Not on PyPI yet.** `pip install ronin-cli` / `uv tool install ronin-cli` are not available until `PYPI_TOKEN` is set and the release workflow publishes. The working install today is the curl installer (now fixed) or `git clone + uv sync + uv run ronin`. Documented honestly.
- `ronin doctor` does not check the Python version or uv/git presence (only provider/model/auth).
- `ronin update` always tracks `origin/main` (ignores a pinned `--ref`).
- `pnpm-workspace.yaml` has a leftover `allowBuilds` placeholder line.

## Verdict

No remaining launch **blockers**. Remaining items are **follow-ups** (PyPI publish, doctor env checks) that do not prevent a release candidate. See `v1_release_notes.md` and `v1_launch_checklist.md`.
