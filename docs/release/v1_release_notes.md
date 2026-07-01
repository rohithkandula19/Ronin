# ronin — v1.0 Release Notes (Release-Candidate)

**Recommended tag:** `v1.0.0-rc.1` (release candidate — see "Versioning decision"). **Current version:** 1.0.0rc1 (PEP 440) · displays as v1.0.0-rc.1.

ronin is a masterless, terminal-native, **provider-agnostic** AI coding agent — Claude-Code-style, but free-first and safety-gated. This document summarizes what's in the v1 line and what remains before a final `v1.0.0`.

## Highlights

- **Free-first, provider-agnostic.** Runs for $0 on Gemini / Groq / Cerebras / OpenRouter / Ollama, or paid on Claude / OpenAI. Switch provider/model in-session (`/provider`, `/free`, `/login`, `/model`).
- **Safety by construction.** Every write and shell command is gated (diff preview + `y/N`); payments/destructive ops hard-blocked in the outward path; a true `--offline` mode strips all network tools; **no telemetry**.
- **The coding agent** (`ronin code`) — streaming Markdown, live plan tracker, syntax-highlighted diffs, a chip strip (`[FREE] [provider:model] [mode] [branch*] [write-gated]`), and six **roles** (`/role`), read-only ones enforced.
- **The verification pipeline** (`ronin pipeline`) — sequential, gated role handoffs with typed artifacts, real-diff evidence (incl. untracked files), independent multi-suite verification (required/optional), contract + optional semantic checks, a Final Verification truth table, gated commit/PR, and git-safe resume with checkpoint restore.
- **Batteries** — 37 in-session slash commands, MCP integrations, a 31-game arcade (`ronin play`), an eval suite with a SWE-bench execution harness.
- **Reference-quality codebase** — 7 packages, **3,274 offline tests**, green in CI.

## What changed leading into v1 (Waves 4–10)

- Waves 4–8: the role-handoff pipeline, structured artifacts, a real verifier, gated commit/PR, independent verification, contract checks, git-safe resume, diff evidence, multi-suite verification, and checkpoint restore.
- Wave 9: untracked-file diff evidence (read-only, never staged), required vs optional suites, and restore-any-checkpoint.
- Wave 10 (this release prep): fixed the broken `install.sh` shim (`csk` → `ronin`/`ro`), added `ronin --version`, corrected stale test-count claims (→ 3,274), and produced the release audit / eval / security docs.

See `CHANGELOG.md` for the full per-wave detail.

## Versioning decision

**Recommendation: cut a release _candidate_ (`v1.0.0-rc.1`), not a final `v1.0.0` yet.** Rationale:

1. **Not on PyPI.** `pip install ronin-cli` / `uv tool install` aren't available until `PYPI_TOKEN` is set and the release workflow publishes. The install path today is the (now-fixed) curl script or `git clone + uv sync`.
2. **Clean-machine install unverified.** `install.sh` was fixed and syntax-checked, but a real from-scratch curl install on a fresh box hasn't been exercised in this environment.
3. **One documented safety boundary** (`--god-mode` destructive-floor, see the security review) is a fair follow-up before a "1.0 final" stamp.

None of these block a **release candidate**; all are honest follow-ups for `v1.0.0` final.

## Known limitations

- Not published to PyPI yet.
- `--god-mode` auto-approves any command (documented, opt-in, loud warning).
- No published SWE-bench score (the harness ships; no number is claimed).
- `ronin doctor` checks provider/model/auth but not the Python/uv environment.

## Not done here (by policy)

No tag, no GitHub release, no PyPI publish, no deploy. Those are the maintainer's explicit calls — see `v1_launch_checklist.md` for the exact commands.
