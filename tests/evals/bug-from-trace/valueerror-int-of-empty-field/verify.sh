#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import subprocess
import sys

proc = subprocess.run([sys.executable, "bin/ports"], capture_output=True, text=True)
assert proc.returncode == 0, "bin/ports still fails:\n" + proc.stderr
assert proc.stdout.strip() == "80,443,8080", proc.stdout

from ports.parse import parse_ports

assert parse_ports("") == []
assert parse_ports(",,") == []
assert parse_ports(" 22 ") == [22]
assert parse_ports("1,2,") == [1, 2]
try:
    parse_ports("1,x")
except ValueError:
    pass
else:
    raise AssertionError("a genuinely non-numeric entry must still raise ValueError")
PYCHECK
[ $? -eq 0 ] || fail "the traced failure is not fixed"
exit 0
