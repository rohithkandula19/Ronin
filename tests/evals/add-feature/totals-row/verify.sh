#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from ledger.report import render

assert render([]) == []
got = render([("cash", 100), ("receivables", 2500)])
assert got[:2] == ["cash 100", "receivables 2500"], got
assert got[2] == "-" * len("receivables 2500"), repr(got[2])
assert got[3] == "TOTAL 2600", got[3]
assert len(got) == 4, got
one = render([("a", -5)])
assert one == ["a -5", "----", "TOTAL -5"], one
PYCHECK
[ $? -eq 0 ] || fail "the specified behaviour is missing or wrong"
exit 0
