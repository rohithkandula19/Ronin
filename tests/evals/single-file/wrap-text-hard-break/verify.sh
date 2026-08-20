#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from render.wrap import wrap

assert wrap("a bb ccc", 6) == ["a bb", "ccc"], wrap("a bb ccc", 6)
got = wrap("short abcdefghij tail", 4)
assert all(len(line) <= 4 for line in got), got
assert "".join(got).replace(" ", "") == "shortabcdefghijtail", got
assert wrap("", 5) == []
assert wrap("abcdef", 3) == ["abc", "def"], wrap("abcdef", 3)
PYCHECK
[ $? -eq 0 ] || fail "render/wrap.py does not behave as the task asked"
exit 0
