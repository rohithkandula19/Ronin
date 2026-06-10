#!/usr/bin/env bash
#
# Release a new version of csk to PyPI via the tag-triggered GitHub Actions workflow.
#
# Usage:
#   scripts/release.sh 0.2.0           # bump, commit, tag v0.2.0, push, watch CI
#   scripts/release.sh 0.2.0 --dry-run # show what would happen, don't push
#
# What this does, in order:
#   1. Pre-flight checks: clean working tree, on main, gh authed, optional PyPI token check
#   2. Run the full test suite — abort on any failure
#   3. Bump version in packages/cli/pyproject.toml + packages/cli/src/.../__init__.py
#   4. Bump test count badge in README (best effort — counts actual passing tests)
#   5. Update CHANGELOG.md (replace [Unreleased] header with [X.Y.Z] — today's date)
#   6. Commit, tag vX.Y.Z, push branch + tag
#   7. Watch the Release workflow run
#
# Prerequisites (one-time):
#   - gh CLI installed + authed (`gh auth login`)
#   - PYPI_TOKEN set as a GitHub Actions secret (Settings → Secrets → Actions → New)
#       Generate at https://pypi.org/manage/account/token/
#   - uv installed
set -euo pipefail

# ---------- colors ----------
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_CYAN=$'\033[36m'
else
  C_RESET=''; C_BOLD=''; C_DIM=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_CYAN=''
fi

step() { echo "${C_BOLD}${C_CYAN}▸ $*${C_RESET}"; }
ok()   { echo "  ${C_GREEN}✓${C_RESET} $*"; }
warn() { echo "  ${C_YELLOW}!${C_RESET} $*"; }
die()  { echo "  ${C_RED}✗${C_RESET} $*" >&2; exit 1; }

# ---------- args ----------
VERSION="${1:-}"
DRY_RUN="no"
for arg in "${@:2}"; do
  case "$arg" in
    --dry-run) DRY_RUN="yes" ;;
    *) die "unknown arg: $arg" ;;
  esac
done

[[ -n "$VERSION" ]] || die "usage: $0 <version> [--dry-run]   (e.g. $0 0.2.0)"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9.]+)?$ ]] || die "version must be semver-ish: $VERSION"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "${C_BOLD}Releasing csk v${VERSION}${C_RESET}"
[[ "$DRY_RUN" == "yes" ]] && echo "${C_YELLOW}(dry run — no commits, no push)${C_RESET}"
echo

# ---------- pre-flight ----------
step "Pre-flight checks"

# Clean tree
if [[ -n "$(git status --porcelain)" ]]; then
  git status --short
  die "working tree is not clean — commit or stash first"
fi
ok "clean working tree"

# On main
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[[ "$BRANCH" == "main" ]] || die "not on main (on '$BRANCH'). Switch to main and re-run."
ok "on main"

# gh authed
if ! command -v gh >/dev/null; then
  die "gh CLI not installed. brew install gh"
fi
if ! gh auth status >/dev/null 2>&1; then
  die "gh not authed. Run: gh auth login"
fi
ok "gh CLI authed"

# Tag doesn't already exist
if git rev-parse "v${VERSION}" >/dev/null 2>&1; then
  die "tag v${VERSION} already exists locally. Pick a different version."
fi
if gh api "repos/:owner/:repo/git/refs/tags/v${VERSION}" >/dev/null 2>&1; then
  die "tag v${VERSION} already exists on origin. Pick a different version."
fi
ok "tag v${VERSION} is free"

# PyPI token configured (warn, don't fail — release script can still tag/push)
if gh secret list 2>/dev/null | grep -q "^PYPI_TOKEN"; then
  ok "PYPI_TOKEN repo secret present"
else
  warn "PYPI_TOKEN repo secret not set. The release workflow will skip publishing."
  warn "Set it at: $(gh repo view --json url -q .url)/settings/secrets/actions"
fi

# ---------- test ----------
step "Running full test suite (one shot)"
TEST_OUTPUT="$(uv run pytest \
  packages/agent-patterns \
  packages/eval-suite \
  packages/memory \
  packages/hardening \
  packages/mcp-servers \
  packages/cli \
  apps/demo \
  -q 2>&1)" || { echo "$TEST_OUTPUT"; die "tests failed — aborting"; }

# Extract passing count, e.g. "242 passed"
PASS_COUNT="$(echo "$TEST_OUTPUT" | grep -oE '[0-9]+ passed' | head -1 | awk '{print $1}')"
ok "${PASS_COUNT:-?} tests passed"

# ---------- version bumps ----------
step "Bumping version → ${VERSION}"

bump_in_file() {
  local file="$1" pattern="$2" replacement="$3"
  if ! grep -q "$pattern" "$file"; then
    die "couldn't find expected pattern in $file"
  fi
  if [[ "$DRY_RUN" == "yes" ]]; then
    ok "[dry] would patch $file"
  else
    # macOS / BSD sed compatibility
    sed -i.bak -E "s|$pattern|$replacement|" "$file"
    rm -f "${file}.bak"
    ok "patched $file"
  fi
}

bump_in_file \
  "packages/cli/pyproject.toml" \
  '^version = "[^"]+"' \
  "version = \"${VERSION}\""

bump_in_file \
  "packages/cli/src/ronin_cli/__init__.py" \
  '^__version__ = "[^"]+"' \
  "__version__ = \"${VERSION}\""

# Tests badge in README (only if count changed)
if [[ -n "${PASS_COUNT:-}" ]]; then
  bump_in_file \
    "README.md" \
    'tests-[0-9]+%20passing' \
    "tests-${PASS_COUNT}%20passing"
fi

# CHANGELOG: replace [Unreleased] with [VERSION] — YYYY-MM-DD, and add a fresh
# [Unreleased] header above it for next time.
TODAY="$(date -u +%Y-%m-%d)"
if [[ "$DRY_RUN" == "yes" ]]; then
  ok "[dry] would update CHANGELOG.md"
else
  awk -v ver="$VERSION" -v today="$TODAY" '
    BEGIN { done=0 }
    /^## \[Unreleased\]/ && !done {
      print "## [Unreleased]"
      print ""
      print "_no changes yet_"
      print ""
      print "## [" ver "] — " today
      done=1
      next
    }
    { print }
  ' CHANGELOG.md > CHANGELOG.md.new
  mv CHANGELOG.md.new CHANGELOG.md
  ok "updated CHANGELOG.md"
fi

# ---------- commit + tag + push ----------
step "Committing + tagging"

if [[ "$DRY_RUN" == "yes" ]]; then
  warn "dry run — stopping before commit. Files patched in place; revert with: git checkout -- ."
  exit 0
fi

git add packages/cli/pyproject.toml \
        packages/cli/src/ronin_cli/__init__.py \
        README.md \
        CHANGELOG.md

git commit -m "release: v${VERSION}

$(cat <<EOF
- bumped CLI to ${VERSION}
- tests: ${PASS_COUNT:-?} passing
- changelog cut

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
ok "committed version bump"

git tag -a "v${VERSION}" -m "csk v${VERSION}"
ok "tagged v${VERSION}"

git push origin main
git push origin "v${VERSION}"
ok "pushed branch + tag"

# ---------- watch release workflow ----------
step "Watching Release workflow"
echo "  (this can take 1–3 min — the workflow builds all packages and publishes to PyPI)"
echo

sleep 5
RUN_ID="$(gh run list --workflow=release.yml --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null || true)"
if [[ -z "$RUN_ID" ]]; then
  warn "couldn't find the release run yet — watch manually:"
  echo "  gh run watch  # or"
  echo "  $(gh repo view --json url -q .url)/actions"
  exit 0
fi

gh run watch "$RUN_ID" --exit-status || die "release workflow failed — see logs"
ok "release v${VERSION} published 🎉"

echo
echo "${C_BOLD}Next:${C_RESET}"
echo "  • verify on PyPI: https://pypi.org/project/ronin-cli/${VERSION}/"
echo "  • test the install: ${C_CYAN}pipx install ronin-cli==${VERSION}${C_RESET}"
echo "  • walk through scripts/launch_kit/DAY_BY_DAY_PLAN.md"
