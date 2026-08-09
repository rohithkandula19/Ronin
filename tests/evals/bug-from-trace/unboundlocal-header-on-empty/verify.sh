#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import subprocess
import sys

proc = subprocess.run([sys.executable, "bin/table"], capture_output=True, text=True)
assert proc.returncode == 0, "bin/table still fails:\n" + proc.stderr
assert proc.stdout == "2 rows\na=1\nb=2\n0 rows\n", repr(proc.stdout)

from tabular.render import render

assert render([]) == "0 rows", repr(render([]))
assert render([("k", "v")]) == "1 rows\nk=v"
PYCHECK
[ $? -eq 0 ] || fail "the traced failure is not fixed"
exit 0
