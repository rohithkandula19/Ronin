#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from prune.plan import prune

calls = []
paths = ["b.tmp", "keep.txt", "a.tmp"]

calls.clear()
assert prune(paths, calls.append) == ["a.tmp", "b.tmp"], prune(paths, calls.append)
calls.clear()
assert prune(paths, calls.append) == ["a.tmp", "b.tmp"]
assert calls == ["a.tmp", "b.tmp"], calls

calls.clear()
assert prune(paths, calls.append, dry_run=True) == ["a.tmp", "b.tmp"]
assert calls == [], calls

calls.clear()
assert prune(["x.txt"], calls.append) == []
assert calls == []

try:
    prune(paths, calls.append, True)
except TypeError:
    pass
else:
    raise AssertionError("dry_run must be keyword-only")
PYCHECK
[ $? -eq 0 ] || fail "the specified behaviour is missing or wrong"
exit 0
