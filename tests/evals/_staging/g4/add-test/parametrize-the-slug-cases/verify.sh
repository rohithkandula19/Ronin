#!/usr/bin/env bash
# Verifier for add-test/parametrize-the-slug-cases.
#
# A test that cannot fail is worth nothing, and "pytest exited 0" does not
# distinguish `assert True` from a real test. So this script:
#   1. checks a new test was actually added (collected node ids vs the fixture's),
#   2. checks the suite passes as it stands,
#   3. reverts quill/slug.py to the version in broken/ and runs ONLY the new tests,
#      which must now fail,
#   4. restores the workspace.
set -uo pipefail

TASK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="${1:-${RONIN_EVAL_WORKSPACE:-$PWD}}"
TARGET="quill/slug.py"
MIN_NEW=3
NEED_PARAM=1

fail() { echo "verify(add-test/parametrize-the-slug-cases): $1"; exit 1; }
setup_error() { echo "verify(add-test/parametrize-the-slug-cases): TASK SETUP ERROR: $1"; exit 2; }

[ -d "$WS" ] || setup_error "workspace '$WS' is not a directory"
cd "$WS" || setup_error "cannot cd into workspace '$WS'"

if command -v pytest >/dev/null 2>&1; then
  PYTEST=(pytest)
elif python3 -c "import pytest" >/dev/null 2>&1; then
  PYTEST=(python3 -m pytest)
else
  setup_error "no pytest available (tried 'pytest' and 'python3 -m pytest')"
fi

[ -f "$TASK_DIR/broken/$TARGET" ] || setup_error "missing broken/$TARGET next to verify.sh"
[ -f "$TASK_DIR/baseline_nodeids.txt" ] || setup_error "missing baseline_nodeids.txt next to verify.sh"
[ -f "$TARGET" ] || fail "$TARGET is gone from the workspace; the implementation must stay in place"

clean_caches() {
  find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
  rm -rf .pytest_cache 2>/dev/null
  return 0
}

collect_ids() { "${PYTEST[@]}" --collect-only -q 2>/dev/null | grep '::' | LC_ALL=C sort -u; }

clean_caches
NOW_IDS="$(collect_ids)"
[ -n "$NOW_IDS" ] || fail "pytest --collect-only found no tests at all in the workspace"

BASE_IDS="$(LC_ALL=C sort -u "$TASK_DIR/baseline_nodeids.txt")"
MISSING="$(LC_ALL=C comm -23 <(printf '%s\n' "$BASE_IDS") <(printf '%s\n' "$NOW_IDS") | tr '\n' ' ')"
if [ -n "${MISSING// /}" ]; then
  fail "pre-existing test(s) were deleted or renamed rather than a new test being added: ${MISSING}"
fi

ADDED="$(LC_ALL=C comm -13 <(printf '%s\n' "$BASE_IDS") <(printf '%s\n' "$NOW_IDS"))"
if [ -z "$ADDED" ]; then
  fail "no test was added: the collected test ids are exactly the fixture's"
fi
N_ADDED="$(printf '%s\n' "$ADDED" | grep -c '::')"
if [ "$NEED_PARAM" = "1" ]; then
  N_PARAM="$(printf '%s\n' "$ADDED" | grep -c '\[')"
  if [ "$N_PARAM" -lt "$MIN_NEW" ]; then
    fail "expected at least $MIN_NEW parametrized cases (node ids of the form test_x[case]) among the new tests, found $N_PARAM parametrized case(s) in $N_ADDED new test id(s)"
  fi
elif [ "$N_ADDED" -lt "$MIN_NEW" ]; then
  fail "expected at least $MIN_NEW new test(s), found $N_ADDED"
fi

# 1. the suite, with the new test in it, must pass as shipped.
clean_caches
SUITE_OUT="$("${PYTEST[@]}" -q 2>&1)"
SUITE_RC=$?
if [ "$SUITE_RC" -ne 0 ]; then
  fail "the test suite does not pass as it stands (pytest exit $SUITE_RC): $(printf '%s' "$SUITE_OUT" | tail -n 1)"
fi

# 2. mutation check: revert the implementation, run ONLY the new tests.
BACKUP="$(mktemp)" || setup_error "mktemp failed"
cp "$TARGET" "$BACKUP" || setup_error "could not back up $TARGET"
cp "$TASK_DIR/broken/$TARGET" "$TARGET" || setup_error "could not install broken/$TARGET"
clean_caches
mapfile -t NEW_IDS < <(printf '%s\n' "$ADDED")
MUT_OUT="$("${PYTEST[@]}" -q "${NEW_IDS[@]}" 2>&1)"
MUT_RC=$?
cp "$BACKUP" "$TARGET" || setup_error "could not restore $TARGET from $BACKUP"
rm -f "$BACKUP"
clean_caches

if [ "$MUT_RC" -eq 0 ]; then
  fail "the new test(s) still pass when $TARGET is reverted to the pre-fix version, so they do not test the thing the task asked about"
fi
if [ "$MUT_RC" -ne 1 ]; then
  fail "with $TARGET reverted the new test(s) neither passed nor plainly failed (pytest exit $MUT_RC): $(printf '%s' "$MUT_OUT" | tail -n 1)"
fi
exit 0
