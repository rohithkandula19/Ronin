#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from find.search import find

lines = ["Alpha", "beta", "ALPHAbet", "gamma"]
assert find(lines, "Alpha") == ["Alpha"], find(lines, "Alpha")
assert find(lines, "alpha") == [], find(lines, "alpha")
assert find(lines, "alpha", fold_case=True) == ["Alpha", "ALPHAbet"], find(
    lines, "alpha", fold_case=True
)
assert find(lines, "BETA", fold_case=True) == ["beta"]
assert find(lines, "") == lines
assert find(lines, "", fold_case=True) == lines
try:
    find(lines, "a", True)
except TypeError:
    pass
else:
    raise AssertionError("fold_case must be keyword-only")
PYCHECK
[ $? -eq 0 ] || fail "the specified behaviour is missing or wrong"
exit 0
