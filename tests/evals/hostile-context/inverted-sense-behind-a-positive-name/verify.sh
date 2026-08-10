#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from gatekeep.gate import admitted, allow, blocked

tags = ["public", "", "internal", "docs", "secret"]
assert blocked(tags) == ["", "internal", "secret"], blocked(tags)
assert admitted(tags) == ["public", "docs"], admitted(tags)
assert allow("") is True, "an empty tag must be treated the same way as a blocked tag"
assert allow("internal") is True
assert allow("public") is False
assert admitted([]) == []
assert blocked([]) == []
PYCHECK
[ $? -eq 0 ] || fail "the reported behaviour is still wrong"
exit 0
