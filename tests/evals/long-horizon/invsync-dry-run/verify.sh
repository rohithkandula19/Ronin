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
from invsync.diff import compute, index
from invsync.records import Record
from invsync.report import render

a = Record(sku="a", name="A", qty=1)
b = Record(sku="b", name="B", qty=2)
diff = compute([a], [b])
target = index([a])

real = apply(target, diff)
assert sorted(real) == ["b"], sorted(real)
preview = apply(target, diff, dry_run=True)
assert preview == target, preview
assert preview is not target, "apply must still return a new mapping"
assert sorted(target) == ["a"], "target must not be mutated"

assert render(diff) == ["added 1", "updated 0", "removed 1"], render(diff)
dry = render(diff, dry_run=True)
assert dry == ["dry run: no changes written", "added 1", "updated 0", "removed 1"], dry

try:
    apply(target, diff, True)
except TypeError:
    pass
else:
    raise AssertionError("dry_run must be keyword-only on apply")
try:
    render(diff, True)
except TypeError:
    pass
else:
    raise AssertionError("dry_run must be keyword-only on render")


def run(*args):
    return subprocess.run(
        [sys.executable, "bin/invsync", *args], capture_output=True, text=True
    )


plain = run("sync")
assert plain.returncode == 0, plain.stderr
assert plain.stdout.splitlines() == ["added 1", "updated 1", "removed 1", "total 3"], (
    plain.stdout
)

previewed = run("sync", "--dry-run")
assert previewed.returncode == 0, previewed.stderr
assert previewed.stdout.splitlines() == [
    "dry run: no changes written",
    "added 1",
    "updated 1",
    "removed 1",
    "total 3",
], previewed.stdout

bad = run("sync", "--nope")
assert bad.returncode == 2, (bad.returncode, bad.stderr)
assert "usage" in bad.stderr, bad.stderr
PYCHECK
[ $? -eq 0 ] || fail "the change is incomplete across the layers"
exit 0
