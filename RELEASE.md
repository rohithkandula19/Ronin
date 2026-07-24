# Releasing ronin to PyPI (`pip install ronin-cli`)

This is the end-to-end runbook for publishing ronin so anyone can run:

```bash
pip install ronin-cli      # or: pipx install ronin-cli
ronin --help
```

ronin is a **uv workspace** that publishes **6 packages together** at the same
version (currently `1.0.0rc2`). The CLI (`ronin-cli`) pins its 5 siblings by exact
version, so **all six must land on PyPI in the same release** or `pip install
ronin-cli` cannot resolve:

| PyPI name              | workspace path           | role                          |
|------------------------|--------------------------|-------------------------------|
| `ronin-cli`            | `packages/cli`           | the CLI + `ronin`/`ro` scripts |
| `ronin-agent-patterns` | `packages/agent-patterns`| agent framework               |
| `ronin-hardening`      | `packages/hardening`     | guards / safety               |
| `ronin-memory`         | `packages/memory`        | memory store                  |
| `ronin-mcp-servers`    | `packages/mcp-servers`   | MCP integrations              |
| `ronin-eval-suite`     | `packages/eval-suite`    | evals                         |

The `ronin` package at the repo root is the workspace meta-package and is **not**
published (it has no build target / is not in the release workflow).

---

## What's already done (verified)

- `packages/cli/pyproject.toml` builds a correct wheel: all 217 `ronin_cli` modules
  ship, including the 30-game `ronin_cli.games` subpackage and the panda art
  modules. There are **no runtime data files** to package (the panda art and games
  are pure `.py`; everything the CLI reads at runtime is user/project files).
- Entry points are wired: `ronin` and `ro` → `ronin_cli.main:app`.
- A built wheel installs into a **clean venv** and `ronin --help`, `ronin version`,
  and `import ronin_cli.games` / `ronin_cli.main` all work.
- Optional extras: `ronin-cli[browser]` (Playwright), `ronin-cli[postgres]`
  (psycopg2), and `ronin-cli[server]` (FastAPI + uvicorn for `ronin serve` /
  `ronin dashboard`). The base install does **not** pull these — `ronin serve`
  needs `pip install 'ronin-cli[server]'`.
- All 6 PyPI names were available (unregistered) as of this writing.
- The release workflow (`.github/workflows/release.yml`) is wired to build + publish
  all 6 on a `v*` tag, gated on the test suite. It just needs the `PYPI_TOKEN` secret.

---

## To go live — what YOU must do

### 1. Create a PyPI account

- Sign up at https://pypi.org/account/register/ and verify your email.
- Enable 2FA (PyPI requires it for accounts that own projects).

You do **not** need to pre-create the projects. The first successful upload of each
of the 6 packages auto-creates that project under your account (you become owner).

> Optional dry run first: do the **same flow on https://test.pypi.org** (separate
> account + token) to rehearse without burning the real name. See "TestPyPI" below.

### 2. Generate a PyPI API token

- https://pypi.org/manage/account/token/ → **Add API token**.
- For the very first publish, the project doesn't exist yet, so scope the token to
  **"Entire account"** (you can't scope to a not-yet-created project).
- Copy the token — it starts with `pypi-...` and is shown **once**.
- (Hardening, later: after the first release, create per-project tokens and delete
  the account-wide one.)

### 3. Add the token as a GitHub repo secret

The workflow reads it as `PYPI_TOKEN` (mapped to `UV_PUBLISH_TOKEN`).

- GitHub web UI: repo **rohithkandula19/Ronin** → Settings → Secrets and variables
  → Actions → **New repository secret** → name `PYPI_TOKEN`, value = the `pypi-...`
  token.
- Or via CLI:
  ```bash
  gh secret set PYPI_TOKEN --repo rohithkandula19/Ronin
  # paste the pypi-... token at the prompt
  ```

### 4. Fire the release (tag `vX.Y.Z`)

The workflow triggers on any tag matching `v*`. The version it publishes is whatever
is in the `pyproject.toml` files (**currently `1.0.0rc2`** in all 6 — keep the tag in
sync with that). To release the current `1.0.0rc2`:

```bash
cd $(git rev-parse --show-toplevel)
git tag v1.0.0-rc.2
git push origin v1.0.0-rc.2
```

The workflow will:
1. `uv sync --all-packages --all-groups` and run `pytest packages -q` (publish gate —
   if tests fail, nothing publishes).
2. Build all 6 packages into `dist/`.
3. `twine check dist/*`.
4. `uv publish dist/*` using `PYPI_TOKEN`. (If the secret is missing it logs
   "Skipping publish" and exits 0 — so a missing token is a no-op, not a failure.)

You can also run it manually without a tag: GitHub → Actions → **Release** →
**Run workflow** (`workflow_dispatch`). Note `workflow_dispatch` publishes the
versions currently in `pyproject.toml`, so still bump versions first for a real release.

### 5. Verify it's live

```bash
# fresh shell, no dev tree:
pipx install ronin-cli        # or: python -m pip install ronin-cli
ronin --help
ronin version                 # -> ronin 1.0.0-rc.2
```

Project pages: `https://pypi.org/project/ronin-cli/` (and the 5 siblings).

---

## Cutting a NEW version later

PyPI is immutable — you can never re-upload the same version. For each release:

1. Bump the version in **all 6** `packages/*/pyproject.toml` AND the 5 pinned
   `ronin-*==X.Y.Z` lines in `packages/cli/pyproject.toml`, AND the
   `__version__` in `packages/cli/src/ronin_cli/__init__.py`. (They are currently
   all `1.0.0rc2` — keep them in lockstep.)
2. Commit.
3. Tag `vX.Y.Z` and push the tag (step 4 above).

---

## Manual fallback (publish from your laptop, no CI)

If you'd rather not use the GitHub workflow, publish locally. **You still need the
PyPI account + an API token** (step 1–2 above); you just don't add it to GitHub.

```bash
cd $(git rev-parse --show-toplevel)

# 1. Build all 6 packages into a clean dist/ (order doesn't matter for building).
rm -rf dist
for pkg in agent-patterns eval-suite memory mcp-servers hardening cli; do
  uv build --package "ronin-$pkg" --out-dir dist
done

# 2. Sanity-check the artifacts.
uvx twine check dist/*

# 3a. Publish with uv (token via env var; same one the CI uses):
export UV_PUBLISH_TOKEN=pypi-XXXXXXXX...
uv publish dist/*

# 3b. OR publish with twine instead:
#   export TWINE_USERNAME=__token__
#   export TWINE_PASSWORD=pypi-XXXXXXXX...
#   uvx twine upload dist/*
```

Publish order: because `ronin-cli` depends on the 5 siblings at an exact version,
upload the 5 siblings **before** `ronin-cli` if you upload one at a time. `uv publish
dist/*` / `twine upload dist/*` upload them all in one shot, which is fine — the
dependency only needs to be resolvable at *install* time, and by the time the upload
finishes all 6 are present.

### TestPyPI rehearsal (recommended for the first ever release)

```bash
# token from https://test.pypi.org/manage/account/token/
export UV_PUBLISH_TOKEN=pypi-TESTPYPI-TOKEN
uv publish --publish-url https://test.pypi.org/legacy/ dist/*

# then install from TestPyPI, pulling normal deps from real PyPI:
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            ronin-cli
ronin --help
```

---

## Local proof (how this was validated)

Reproduce the clean-venv test end-to-end without publishing:

```bash
cd $(git rev-parse --show-toplevel)
rm -rf dist_release
for p in agent-patterns eval-suite memory mcp-servers hardening cli; do
  uv build --package "ronin-$p" --out-dir dist_release
done
uvx twine check dist_release/*

# clean venv OUTSIDE the dev tree, install the wheel resolving siblings from dist_release:
WORK=$(mktemp -d) && uv venv --seed "$WORK/.venv"
"$WORK/.venv/bin/python" -m pip install \
  --find-links dist_release \
  dist_release/ronin_cli-1.0.0rc2-py3-none-any.whl

cd /tmp   # leave the source tree so nothing is shadowed
"$WORK/.venv/bin/ronin" --help
"$WORK/.venv/bin/ronin" version
"$WORK/.venv/bin/python" -c "import ronin_cli.games, ronin_cli.main; print(len(ronin_cli.games.GAMES), 'games')"
rm -rf "$WORK"
```

> `dist_release/` is a throwaway scratch dir used for local testing; the CI builds
> into `dist/`. Delete `dist_release/` whenever; it is not the published artifact.

---

## Quick reference

| Thing                     | Value                                                  |
|---------------------------|--------------------------------------------------------|
| Install command           | `pip install ronin-cli` (or `pipx install ronin-cli`)  |
| CLI entry points          | `ronin`, `ro`                                           |
| GitHub repo               | `rohithkandula19/Ronin`                                |
| Release workflow          | `.github/workflows/release.yml`                        |
| Trigger                   | push tag `v*`  (or Actions → Release → Run workflow)   |
| Required secret           | `PYPI_TOKEN`  (a `pypi-...` API token)                  |
| Token env var (manual)    | `UV_PUBLISH_TOKEN` (uv) / `TWINE_PASSWORD` (twine)      |
| Packages published        | all 6 in the table above, same version                 |
| Current version           | `1.0.0rc2`                                                |
| Optional extras           | `[browser]`, `[postgres]`, `[server]`                  |
