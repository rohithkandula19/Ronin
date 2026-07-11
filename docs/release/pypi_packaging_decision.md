# PyPI Packaging Decision

**Decision: Option A — publish the internal packages independently to PyPI, in
the existing structure.** No restructuring, no bundling, no duplication.

## Why this was needed

Phase 1 RC validation found `pip install <ronin_cli wheel>` fails standalone:
`No matching distribution found for ronin-agent-patterns==1.0.0rc2`. Root cause
(see `pypi_dependency_graph.md`): `ronin-cli` correctly declares five internal
dependencies as version specs, but those packages are not on any index yet, so a
lone cli wheel can't resolve them.

## Options considered

| Option | Advantages | Disadvantages | Migration risk | Rollback | Verdict |
|---|---|---|---|---|---|
| **A. Publish internal packages independently** (current structure) | Matches the design ("seven packages usable independently"); metadata already correct; zero code change; no import duplication; libraries remain reusable | Six PyPI releases to keep version-synced; publish order matters on first upload | **None** (structure unchanged) | trivial | **SELECTED** |
| **B. Consolidate all internal packages into `ronin-cli`** | Single wheel installs with no ronin deps | **Breaks** the independent-library design; if libraries are still published → **import duplication** (two copies of `ronin_hardening`, …); large refactor | high | hard | rejected |
| **C. Bundle selected modules into `ronin-cli`, keep source separation** | One wheel for the common case | Duplicate module copies → import ambiguity (violates "no duplication"); version skew between copies | high | hard | rejected |
| **D. Public metapackage + independent internal packages** | Clean "install everything" name | More moving parts than A for no added benefit here; extra distribution to own | medium | medium | rejected (A is smaller) |

## Why Option A (the smallest safe architecture)

It satisfies every selection criterion with **no code change**:
- **Standalone install:** provided the wheel set (now) or PyPI (after publish),
  `pip install ronin-cli` resolves all five internal deps automatically —
  **proven** by `scripts/test_clean_install.sh` (clean venv, outside the repo,
  no source tree, internal wheels local + external deps from real PyPI).
- **No import duplication / no monorepo at runtime:** each package ships once;
  the installed cli imports the installed libraries.
- **Local development unaffected:** `[tool.uv.sources] { workspace = true }`
  keeps dev resolving from the workspace; it is stripped from built artifacts.
- **Future PyPI releases:** `.github/workflows/release.yml` already builds all
  packages, runs `twine check dist/*`, and `uv publish`es them (gated on
  `PYPI_TOKEN`). Rules respected: **no dependency pin was weakened**, **no
  package was duplicated**, **no missing dependency was hidden**.

## What was implemented (not a restructure)

1. `scripts/test_clean_install.sh` — proves standalone install from artifacts.
2. `packages/cli/tests/test_packaging_metadata.py` — regression guard: no
   local-path deps, all internal deps declared, both console scripts present,
   no secret/state files in the wheel.
3. Honest install docs distinguishing GitHub/local-wheel install (now) from
   PyPI `pip install` (after the packages are published — approval-gated).

## Rollback

Nothing structural changed, so there is nothing to roll back. If a future
`uv publish` of one package fails, re-run it — the version specs are exact
(`==1.0.0rcN`), so a partial upload never resolves to a wrong version.

## First-publish order (when PyPI publish is approved)

Publish the five libraries before the cli so `pip install ronin-cli` can resolve
on the first attempt: `ronin-agent-patterns`, `ronin-hardening`, `ronin-memory`,
`ronin-mcp-servers`, `ronin-eval-suite`, then `ronin-cli`. (`uv publish dist/*`
uploads them together, which PyPI indexes atomically enough in practice; the
explicit order is the safe manual fallback.)
