#!/usr/bin/env bash
# Prove `ronin` installs and runs STANDALONE from built artifacts only — in a clean
# venv, outside the repo, with no source tree on the path and no package index (so a
# missing dependency cannot be masked). Exits non-zero on any failure. Set KEEP=1 to
# keep the temp dir for debugging.
#
# This tested the v1 wheel (`ronin_cli`, console script `ronin1`) until the
# consolidation onto a single binary. That was the wrong target twice over: v1 is
# being removed, and the consequence of pointing the only clean-install gate at it
# was that `ronin` — the distribution people actually install — had never been
# installed from a wheel and run even once in CI. Coverage moved to the thing that
# survives.
#
# The zero-dependency assertion below is the one worth reading. `pyproject.toml`
# declares `dependencies = []` and calls it deliberate: every optional capability is
# reached through a lazy import and degrades with a named error, so `pip install
# ronin` is supposed to give a working agent and each extra adds a capability rather
# than repairing a broken one. Nothing checked that. A single stray top-level import
# of httpx or textual in `src/ronin` would have made the promise false while every
# other gate stayed green, because the dev venv has those extras installed.
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
# `ronin-*` and not `ronin_cli-*`: the glob must not match a sibling distribution
# whose name merely starts the same way.
ls "$DIST"/ronin-[0-9]*.whl >/dev/null 2>&1 || fail "no ronin wheel in dist/"

echo "== creating clean venv =="
python3 -m venv "$VENV"
# Critical: no source-tree path leakage, no accidental repo import.
unset PYTHONPATH
"$VENV/bin/pip" install --quiet --upgrade pip

echo "== recording the baseline, before ronin =="
BASELINE="$WORK/baseline.txt"
"$VENV/bin/pip" list --format=freeze | cut -d= -f1 | sort > "$BASELINE"

echo "== installing ONLY from local artifacts (no index) =="
WHEEL="$(ls "$DIST"/ronin-[0-9]*.whl | head -1)"
"$VENV/bin/pip" install --quiet --no-index --find-links "$DIST" "$WHEEL" \
  || fail "install of $WHEEL failed with no package index — a hard dependency crept in"

echo "== the zero-dependency promise =="
AFTER="$WORK/after.txt"
"$VENV/bin/pip" list --format=freeze | cut -d= -f1 | sort > "$AFTER"
ADDED="$(comm -13 "$BASELINE" "$AFTER" | grep -v '^ronin$' || true)"
if [ -n "$ADDED" ]; then
  echo "$ADDED" >&2
  fail "installing ronin pulled in third-party packages; pyproject declares dependencies = []"
fi
echo "  ok: ronin installed alone, exactly as 'dependencies = []' promises"

echo "== console script =="
RONIN="$VENV/bin/ronin"
[ -x "$RONIN" ] || fail "console script 'ronin' missing"
# `ronin2` is declared alongside `ronin` and points at the same entry point. Asserted
# rather than assumed: the two names are what the consolidation is removing, and this
# line is the one that has to change when `ronin` becomes the only one.
[ -x "$VENV/bin/ronin2" ] || fail "console script 'ronin2' missing"
# `if`, not `[ … ] && fail`: under `set -e` a false test at the head of an `&&` list
# makes the list non-zero and kills the script, so a negative assertion would "pass"
# by aborting the run before the smoke tests below ever happened.
for foreign in ronin1 ro; do
  if [ -e "$VENV/bin/$foreign" ]; then
    fail "the ronin wheel claimed '$foreign'; that name is not this distribution's"
  fi
done

echo "== prove the install does NOT import from the source tree =="
SRC_FILE="$(cd "$RUNDIR" && "$VENV/bin/python" -c 'import ronin, sys; sys.stdout.write(ronin.__file__)')"
REPO_REAL="$(cd "$REPO" && pwd -P)"
# Robust to macOS /tmp -> /private symlinks: require an INSTALLED copy (under
# site-packages) that is NOT the repo source tree.
case "$SRC_FILE" in
  */site-packages/ronin/*)
    case "$SRC_FILE" in
      "$REPO_REAL"/*|"$REPO"/*) fail "ronin imported from the repo SOURCE: $SRC_FILE";;
      *) echo "  ok: ronin loads from installed site-packages ($SRC_FILE)";;
    esac ;;
  *) fail "ronin not loaded from site-packages (possible source leakage): $SRC_FILE";;
esac

echo "== smoke, run from OUTSIDE the repo ($RUNDIR) =="
cd "$RUNDIR"
"$RONIN" --version  >/dev/null || fail "ronin --version"
"$RONIN" --help     >/dev/null || fail "ronin --help"
"$RONIN" sessions   >/dev/null || fail "ronin sessions"

# `doctor` exits 1 on a machine with no model configured, and that is correct — it
# reports problems and its exit code says it found some. What matters is that it RAN
# and produced its report rather than dying on a missing optional import, so this
# checks the output, not the status.
DOCTOR_OUT="$("$RONIN" doctor 2>&1 || true)"
case "$DOCTOR_OUT" in
  *"ronin doctor"*) echo "  ok: doctor produced its report on a bare machine";;
  *) echo "$DOCTOR_OUT" >&2; fail "doctor did not produce a report";;
esac

# A capability whose extra is not installed must say so by name. An ImportError
# traceback here would mean the lazy-import contract had been broken somewhere.
case "$DOCTOR_OUT" in
  *Traceback*) echo "$DOCTOR_OUT" >&2; fail "doctor raised a traceback on a bare install";;
esac

# The bare-word prompt path needs a model, so it must fail *cleanly* rather than
# crash: a user's first command on a fresh install is the worst place for a stack
# trace, and with no extras installed it is also the likeliest.
PROMPT_OUT="$("$RONIN" -p "hello" 2>&1 || true)"
case "$PROMPT_OUT" in
  *Traceback*) echo "$PROMPT_OUT" >&2; fail "a prompt with no model configured raised a traceback";;
  *) echo "  ok: a prompt with no model configured failed with prose, not a traceback";;
esac

echo "== clean uninstall =="
"$VENV/bin/pip" uninstall -y ronin >/dev/null || fail "uninstall ronin"
[ -e "$VENV/bin/ronin" ] && fail "console script survived uninstall"

echo "PASS: standalone clean install verified — built artifacts, clean venv, no source tree, no dependencies."
