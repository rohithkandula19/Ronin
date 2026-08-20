#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from usage.table import render

got = render([("docs", 25), ("code", 75)])
assert got == ["docs 25 25.0%", "code 75 75.0%"], got
assert render([]) == []
assert render([("a", 0), ("b", 0)]) == ["a 0 0.0%", "b 0 0.0%"], render([("a", 0), ("b", 0)])
assert render([("only", 3)]) == ["only 3 100.0%"]
thirds = render([("a", 1), ("b", 1), ("c", 1)])
assert thirds == ["a 1 33.3%", "b 1 33.3%", "c 1 33.3%"], thirds
PYCHECK
[ $? -eq 0 ] || fail "the specified behaviour is missing or wrong"
exit 0
