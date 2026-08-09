#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import subprocess
import sys

proc = subprocess.run([sys.executable, "bin/quota"], capture_output=True, text=True)
assert proc.returncode == 0, "bin/quota still fails:\n" + proc.stderr
assert proc.stdout.strip() == "350", proc.stdout

from quota.usage import total_bytes

assert total_bytes([]) == 0
assert total_bytes([{"a": 1}]) == 0
assert total_bytes([{"bytes": 5}, {}, {"bytes": 7}]) == 12
PYCHECK
[ $? -eq 0 ] || fail "the traced failure is not fixed"
exit 0
