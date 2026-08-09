#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import subprocess
import sys

proc = subprocess.run([sys.executable, "bin/leaders"], capture_output=True, text=True)
assert proc.returncode == 0, "bin/leaders still fails:\n" + proc.stderr
assert proc.stdout == "bo\nana\n", repr(proc.stdout)

from leaders.rank import top

rows = [{"name": "a", "score": "2"}, {"name": "b", "score": 10}, {"name": "c", "score": 5}]
assert top(rows, 3) == ["b", "c", "a"], top(rows, 3)
assert top(rows, 1) == ["b"]
assert top([], 3) == []
PYCHECK
[ $? -eq 0 ] || fail "the traced failure is not fixed"
exit 0
