#!/usr/bin/env bash
#
# Prepare and publish a synchronized Ronin release through the tag workflow.
#
# Usage:
#   scripts/release.sh 1.0.1           # bump, commit, tag v1.0.1, push, watch CI
#   scripts/release.sh v1.1.0-rc.1 --dry-run # run gates; do not modify, tag, or push
#
# What this does, in order:
#   1. Pre-flight checks: clean working tree, on main, gh authed, optional PyPI token check
#   2. Run the full test suite — abort on any failure
#   3. Synchronize all published package versions and exact CLI dependency pins
#   4. Update CHANGELOG.md (replace [Unreleased] header with [X.Y.Z] — today's date)
#   5. Commit, tag vX.Y.Z, push branch + tag
#   6. Watch the Release workflow (tests, artifacts, checksums, GitHub release, optional PyPI)
#
# Prerequisites (one-time):
#   - gh CLI installed + authed (`gh auth login`) for non-dry runs
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
INPUT_VERSION="${1:-}"
DRY_RUN="no"
for arg in "${@:2}"; do
  case "$arg" in
    --dry-run) DRY_RUN="yes" ;;
    *) die "unknown arg: $arg" ;;
  esac
done

[[ -n "$INPUT_VERSION" ]] || die "usage: $0 <version> [--dry-run]   (e.g. $0 1.0.1)"
TAG="$INPUT_VERSION"
[[ "$TAG" == v* ]] || TAG="v${TAG}"
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-(a|b|rc)\.[0-9]+)?$ ]] \
  || die "version must look like 1.2.3 or v1.2.3-rc.1: $INPUT_VERSION"
VERSION="${TAG#v}"
PYPI_VERSION="${VERSION/-rc./rc}"
PYPI_VERSION="${PYPI_VERSION/-a./a}"
PYPI_VERSION="${PYPI_VERSION/-b./b}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "${C_BOLD}Releasing Ronin ${TAG}${C_RESET}"
[[ "$DRY_RUN" == "yes" ]] && echo "${C_YELLOW}(dry run — no writes, tags, or pushes)${C_RESET}"
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

if [[ "$DRY_RUN" == "yes" ]]; then
  warn "dry run skips GitHub authentication, remote tag, and secret checks"
else
  if ! command -v gh >/dev/null; then
    die "gh CLI not installed. brew install gh"
  fi
  if ! gh auth status >/dev/null 2>&1; then
    die "gh not authed. Run: gh auth login"
  fi
  ok "gh CLI authed"

  if git rev-parse "$TAG" >/dev/null 2>&1; then
    die "tag $TAG already exists locally. Pick a different version."
  fi
  if gh api "repos/:owner/:repo/git/refs/tags/$TAG" >/dev/null 2>&1; then
    die "tag $TAG already exists on origin. Pick a different version."
  fi
  ok "tag $TAG is free"

  if gh secret list 2>/dev/null | grep -q "^PYPI_TOKEN"; then
    ok "PYPI_TOKEN repo secret present"
  else
    warn "PYPI_TOKEN repo secret not set. Artifacts and a GitHub Release will still be created."
    warn "Set it at: $(gh repo view --json url -q .url)/settings/secrets/actions"
  fi
fi

# ---------- test ----------
step "Running full test suite (one shot)"
TEST_OUTPUT="$(uv run --frozen pytest packages apps training -q 2>&1)" \
  || { echo "$TEST_OUTPUT"; die "tests failed — aborting"; }

# Extract passing count, e.g. "242 passed"
PASS_COUNT="$(echo "$TEST_OUTPUT" | grep -oE '[0-9]+ passed' | head -1 | awk '{print $1}')"
ok "${PASS_COUNT:-?} tests passed"

# ---------- version bumps ----------
step "Synchronizing release versions → ${TAG}"

if [[ "$DRY_RUN" == "yes" ]]; then
  ok "[dry] would update package versions and exact CLI dependency pins"
  ok "[dry] would validate tag/version and artifact manifest in GitHub Actions"
else
  uv run --frozen python -m ronin_cli.release --root "$REPO_ROOT" --prepare "$TAG" --tag "$TAG" \
    || die "release manifest validation failed"
  ok "synchronized release versions and internal dependency pins"
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
  warn "dry run complete — no files were changed."
  exit 0
fi

git add pyproject.toml \
        packages/agent-patterns/pyproject.toml \
        packages/eval-suite/pyproject.toml \
        packages/memory/pyproject.toml \
        packages/hardening/pyproject.toml \
        packages/mcp-servers/pyproject.toml \
        packages/relay/pyproject.toml \
        packages/cli/src/ronin_cli/__init__.py \
        packages/relay/src/ronin_relay/__init__.py \
        packages/cli/pyproject.toml \
        CHANGELOG.md

git commit -m "release: v${VERSION}

$(cat <<EOF
- synchronized published packages to ${VERSION}
- tests: ${PASS_COUNT:-?} passing
- changelog cut
EOF
)"
ok "committed version bump"

git tag -a "$TAG" -m "Ronin $TAG"
ok "tagged $TAG"

git push origin main
git push origin "$TAG"
ok "pushed branch + tag"

# ---------- watch release workflow ----------
step "Watching Release workflow"
echo "  (this can take several minutes — tests, build, clean install, checksums, and release upload)"
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
ok "release workflow completed for $TAG"

echo
echo "${C_BOLD}Next:${C_RESET}"
echo "  • verify GitHub Release artifacts and SHA256SUMS"
echo "  • verify on PyPI when publishing was enabled: https://pypi.org/project/ronin-cli/${PYPI_VERSION}/"
echo "  • test the install: ${C_CYAN}pipx install ronin-cli==${VERSION}${C_RESET}"
echo "  • walk through scripts/launch_kit/DAY_BY_DAY_PLAN.md"
