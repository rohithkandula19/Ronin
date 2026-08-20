#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import subprocess
import sys

proc = subprocess.run([sys.executable, "bin/intake"], capture_output=True, text=True)
assert proc.returncode == 0, "bin/intake still fails:\n" + proc.stderr
assert proc.stdout == "Ada Byron Lovelace\nAlan Turing\n", repr(proc.stdout)

from intake.normalise import full_name

assert full_name({"first": "A", "middle": None, "last": "B"}) == "A B"
assert full_name({"first": " A ", "middle": " ", "last": "B"}) == "A B"
assert full_name({"first": "A", "middle": "M", "last": "B"}) == "A M B"
PYCHECK
[ $? -eq 0 ] || fail "the traced failure is not fixed"
exit 0
