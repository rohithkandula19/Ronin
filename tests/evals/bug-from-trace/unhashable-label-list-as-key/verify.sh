#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import subprocess
import sys

proc = subprocess.run([sys.executable, "bin/labels"], capture_output=True, text=True)
assert proc.returncode == 0, "bin/labels still fails:\n" + proc.stderr
assert proc.stdout == "bug 2\np1 1\n", repr(proc.stdout)

from labelcount.count import count_labels

assert count_labels([]) == {}
assert count_labels([{"labels": []}]) == {}
assert count_labels([{"labels": ["a", "b"]}, {"labels": ["b"]}]) == {"a": 1, "b": 2}
PYCHECK
[ $? -eq 0 ] || fail "the traced failure is not fixed"
exit 0
