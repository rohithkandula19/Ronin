#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import subprocess
import sys

proc = subprocess.run([sys.executable, "bin/pages"], capture_output=True, text=True)
assert proc.returncode == 0, "bin/pages still fails:\n" + proc.stderr
assert proc.stdout == "page 1: a..b\npage 2: c..d\npage 3: e..e\n", repr(proc.stdout)

from paging.page import bounds, page

items = ["a", "b", "c", "d", "e"]
assert page(items, 3, 2) == ["e"]
assert bounds(items, 1, 2) == ("a", "b")
assert bounds(items, 3, 2) == ("e", "e")
try:
    bounds(items, 9, 2)
except ValueError:
    pass
else:
    raise AssertionError("bounds of a page past the end must raise ValueError, not IndexError")
PYCHECK
[ $? -eq 0 ] || fail "the traced failure is not fixed"
exit 0
