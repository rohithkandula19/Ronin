#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from net.backoff import delay_for

assert delay_for(0) == 0.5, delay_for(0)
assert delay_for(1) == 1.0, delay_for(1)
assert delay_for(5) == 16.0, delay_for(5)
assert delay_for(6) == 30.0, delay_for(6)
assert delay_for(20) == 30.0, delay_for(20)
assert delay_for(20, max_delay=5.0) == 5.0
assert delay_for(0, base=2.0, max_delay=1.0) == 1.0
try:
    delay_for(-1)
except ValueError:
    pass
else:
    raise AssertionError("a negative attempt must still raise ValueError")
PYCHECK
[ $? -eq 0 ] || fail "net/backoff.py does not behave as the task asked"
exit 0
