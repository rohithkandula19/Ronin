# ronin — v1.0 Launch Checklist

Status as of 2026-07-01, version 0.59.0. ☑ = done · ☐ = maintainer action.

## Code & tests
- ☑ Full test suite green — **3,274 passed** (`uv run --frozen pytest packages apps -q`).
- ☑ CI runs the same suite on Python 3.12 on every push.
- ☑ Waves 1–9 features merged to `main` (PRs #22–#33).

## Packaging & install
- ☑ `install.sh` fixed — installs `ronin` + `ro` shims (was a broken `csk` shim).
- ☑ `ronin --version` and `ronin version` both work.
- ☑ `ronin update` (git-checkout updater, dirty-tree guard) works.
- ☑ pyproject metadata correct (name, version, MIT, classifiers, urls, `requires-python>=3.11`).
- ☐ **Verify the curl install on a clean machine** (fresh macOS/Linux box) before final 1.0.
- ☐ **Publish to PyPI** — set `PYPI_TOKEN` repo secret; the release workflow then runs `twine check` + `uv publish`. (Optional for RC.)

## Docs
- ☑ README claims reconciled with code; test counts corrected to 3,274.
- ☑ New user docs: `docs/getting-started` (README quickstart), `docs/providers.md`, `docs/free-mode.md`, `docs/offline.md`, `docs/pipeline.md`, `docs/safety.md`.
- ☑ Release docs under `docs/release/`.
- ☑ `QUICKSTART.md`, `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`, `LICENSE`, `CITATION.cff` present and version-accurate.

## Demo & launch assets
- ☑ Demo script (`docs/release/demo_script.md`) — verified commands only.
- ☑ Social / HN / Product Hunt drafts (`docs/release/*.md`) — no fabricated metrics.
- ☑ README GIF/PNG references all resolve to real files.
- ☐ (Optional) Re-record the demo GIF with `vhs docs/demo/demo.tape` after the install fix.

## Security & safety
- ☑ Security review — 12/12 PASS, zero blockers (`docs/release/v1_security_review.md`).
- ☑ Secret scan clean (only fixtures/placeholders).
- ☑ Approval gates, offline mode, read-only roles, gated commit/PR verified.
- ☐ (Follow-up, non-blocking) Add a destructive hard-floor to the code-mode shell under `--god-mode`.

## Eval / benchmark
- ☑ Honest eval report (`docs/release/v1_eval_report.md`) — RUN vs SKIPPED vs NOT-RUN labeled.
- ☑ No fake benchmarks / no published SWE-bench score.

## Release (maintainer — NOT done automatically)

Nothing below has been run. Exact commands when you're ready:

```bash
# 1. (optional) bump version across packages, if going to a real tag
#    e.g. edit pyproject versions or use: ronin release --type <patch|minor|major>

# 2. tag a release candidate (recommended first)
git tag v1.0.0-rc.1
git push origin v1.0.0-rc.1

# 3. create the GitHub release (after RC soak)
gh release create v1.0.0-rc.1 --title "v1.0.0-rc.1" --notes-file docs/release/v1_release_notes.md --prerelease

# 4. publish to PyPI (only after setting PYPI_TOKEN) — the release workflow does this on tag,
#    or manually:  uv build && uv publish
```

**Do not** run these until you've decided to ship. This checklist and all release docs are prepared, not executed.
