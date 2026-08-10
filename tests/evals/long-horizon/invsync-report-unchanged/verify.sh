#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import subprocess
import sys

from invsync.apply import apply
from invsync.diff import Diff, compute, index
from invsync.records import Record
from invsync.report import render

a = Record(sku="a", name="A", qty=1)
b = Record(sku="b", name="B", qty=2)
b2 = Record(sku="b", name="B", qty=9)
c = Record(sku="c", name="C", qty=3)

diff = compute([a, b], [a, b2, c])
assert [r.sku for r in diff.added] == ["c"], diff.added
assert [r.sku for r in diff.updated] == ["b"], diff.updated
assert [r.sku for r in diff.unchanged] == ["a"], diff.unchanged
assert diff.removed == []

assert Diff(added=[], updated=[], removed=[]).unchanged == [], "unchanged must default to []"

assert render(diff) == ["added 1", "updated 1", "unchanged 1", "removed 0"], render(diff)
assert render(Diff(added=[], updated=[], removed=[])) == [
    "added 0",
    "updated 0",
    "unchanged 0",
    "removed 0",
]

target = index([a, b])
result = apply(target, diff)
assert sorted(result) == ["a", "b", "c"], sorted(result)
assert result["b"].qty == 9
assert result["a"] is a, "an unchanged record must not be rewritten"

proc = subprocess.run(
    [sys.executable, "bin/invsync", "sync"], capture_output=True, text=True
)
assert proc.returncode == 0, proc.stderr
lines = proc.stdout.splitlines()
assert lines == ["added 1", "updated 1", "unchanged 1", "removed 1", "total 3"], lines
PYCHECK
[ $? -eq 0 ] || fail "the change is incomplete across the layers"
exit 0
