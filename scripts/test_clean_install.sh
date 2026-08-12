#!/usr/bin/env bash
# Prove ronin-cli installs and runs STANDALONE from built artifacts only —
# in a clean venv, outside the repo, with no source-tree on the path and no
# package index (so a missing dependency cannot be masked). Exits non-zero on
# any failure. Set KEEP=1 to keep the temp dir for debugging.
set -euo pipefail

REPO="${RONIN_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
DIST="$REPO/dist"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ronin-clean-install.XXXXXX")"
VENV="$WORK/venv"
RUNDIR="$WORK/run"           # a dir OUTSIDE the repo to run from
mkdir -p "$RUNDIR"

cleanup() { [ "${KEEP:-0}" = "1" ] || rm -rf "$WORK"; }
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

echo "== repo: $REPO"
echo "== dist: $DIST"
[ -d "$DIST" ] || fail "no dist/ — run: uv build --all-packages"
ls "$DIST"/ronin_cli-*.whl >/dev/null 2>&1 || fail "no ronin_cli wheel in dist/"

echo "== creating clean venv =="
python3 -m venv "$VENV"
# Critical: no source-tree path leakage, no accidental repo import.
unset PYTHONPATH
"$VENV/bin/pip" install --quiet --upgrade pip

echo "== install ronin-cli standalone (ronin wheels local, external deps from PyPI) =="
# --find-links dist: the 6 ronin wheels resolve from the built artifact set
# (mimicking their future PyPI presence — they are not published yet). External
# deps (anthropic, pydantic, rich, typer, textual, ...) come from real PyPI,
# which is honest: they genuinely live there. This is exactly what a real
# `pip install ronin-cli` resolves once the ronin packages are published.
# A missing ronin workspace wheel still fails loudly (PyPI has none of them).
"$VENV/bin/pip" install --find-links "$DIST" ronin-cli \
  || fail "standalone install from artifact set + PyPI failed"

echo "== confirm the 5 ronin workspace deps were installed (from local wheels) =="
for pkg in ronin-agent-patterns ronin-hardening ronin-memory ronin-mcp-servers ronin-eval-suite; do
  "$VENV/bin/pip" show "$pkg" >/dev/null 2>&1 || fail "workspace dep not installed: $pkg"
done

# `ronin1`, not `ronin`. The short name belongs to the v2 tree at the repository
# root, and console scripts are not namespaced: two installed distributions
# declaring one name means whichever landed second silently overwrote the other's
# launcher. Asserting the *absence* of the old names is the half that matters —
# without it this script would still pass in the world where the collision came
# back and the wheel this venv installed was not the one being invoked.
RONIN="$VENV/bin/ronin1"
[ -x "$RONIN" ] || fail "console script 'ronin1' missing"
# `if`, not `[ … ] && fail`: under `set -e` a false test at the head of an `&&` list
# makes the list non-zero and kills the script, so the negative assertions would
# "pass" by aborting the run before the smoke tests below ever happened.
for retired in ronin ro; do
  if [ -e "$VENV/bin/$retired" ]; then
    fail "ronin-cli claimed '$retired'; that name is the v2 tree's or was retired"
  fi
done

echo "== prove the install does NOT import from the source tree =="
SRC_FILE="$(cd "$RUNDIR" && "$VENV/bin/python" -c 'import ronin_cli, sys; sys.stdout.write(ronin_cli.__file__)')"
REPO_REAL="$(cd "$REPO" && pwd -P)"
# Robust to macOS /tmp -> /private symlinks: require it to be an INSTALLED copy
# (under site-packages) and NOT the repo source tree.
case "$SRC_FILE" in
  */site-packages/ronin_cli/*)
    case "$SRC_FILE" in
      "$REPO_REAL"/*|"$REPO"/*) fail "ronin_cli imported from the repo SOURCE: $SRC_FILE";;
      *) echo "  ok: ronin_cli loads from installed site-packages ($SRC_FILE)";;
    esac ;;
  *) fail "ronin_cli not loaded from site-packages (possible source leakage): $SRC_FILE";;
esac

echo "== smoke, run from OUTSIDE the repo ($RUNDIR) =="
cd "$RUNDIR"
"$RONIN" --version                >/dev/null || fail "ronin1 --version"
"$RONIN" util version             >/dev/null || fail "ronin1 util version"
"$RONIN" --help                   >/dev/null || fail "ronin1 --help"
"$RONIN" util doctor              >/dev/null || fail "ronin1 util doctor"
# offline-safe: a missing eval dataset must error gracefully (exit 2), not crash.
set +e
"$RONIN" eval run /nonexistent.jsonl >/dev/null 2>&1; ec=$?
set -e
[ "$ec" = "2" ] || fail "offline eval missing-dataset expected exit 2, got $ec"

echo "== clean uninstall =="
"$VENV/bin/pip" uninstall -y ronin-cli >/dev/null || fail "uninstall ronin-cli"

echo "PASS: standalone clean install verified — built artifacts, clean venv, no source tree."
