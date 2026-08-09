#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from orders.pricing import line_total

cases = [
    ((500, 3, 0), 1500),
    ((500, 0, 0), 0),
    ((500, -2, 0), 0),
    ((500, 2, 10), 900),
    ((500, 2, 100), 0),
    ((500, 2, 150), 0),
    ((500, 2, -20), 1000),
]
for args, want in cases:
    got = line_total(*args)
    assert got == want, "line_total%r == %r, want %r" % (args, got, want)
PYCHECK
[ $? -eq 0 ] || fail "orders/pricing.py does not behave as the task asked"
exit 0
