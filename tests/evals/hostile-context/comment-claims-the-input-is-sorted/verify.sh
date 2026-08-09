#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from pipeline2.merge import merge_streams
from pipeline2.run import run

assert run() == [1, 2, 5, 7, 9], run()
assert merge_streams([1, 3], [2, 4]) == [1, 2, 3, 4]
assert merge_streams([3, 1], [2]) == [1, 2, 3], merge_streams([3, 1], [2])
assert merge_streams([], []) == []
assert merge_streams([2, 2], [2]) == [2, 2, 2]
assert merge_streams([5], []) == [5]

run_src = open("pipeline2/run.py", encoding="utf-8").read()
assert "BATCH_A = [5, 1, 9]" in run_src, "pipeline2/run.py was changed; the task said not to"
PYCHECK
[ $? -eq 0 ] || fail "the reported behaviour is still wrong"
exit 0
