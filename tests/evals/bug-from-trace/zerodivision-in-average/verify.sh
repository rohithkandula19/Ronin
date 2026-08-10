#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import subprocess
import sys

proc = subprocess.run([sys.executable, "bin/latency"], capture_output=True, text=True)
assert proc.returncode == 0, "bin/latency still fails:\n" + proc.stderr
assert proc.stdout == "/health 0.0\n/items 15.0\n/login 5.0\n", repr(proc.stdout)

from metrics.agg import mean, per_route

assert mean([]) == 0.0
assert mean([2.0, 4.0]) == 3.0
assert per_route({}) == []
assert per_route({"b": [], "a": [1.0]}) == [("a", 1.0), ("b", 0.0)]
PYCHECK
[ $? -eq 0 ] || fail "the traced failure is not fixed"
exit 0
