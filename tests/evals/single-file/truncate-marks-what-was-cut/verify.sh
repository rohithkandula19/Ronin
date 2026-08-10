#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from tools.output import truncate

exact = "a\nb\nc\n"
assert truncate(exact, keep=5) == exact, repr(truncate(exact, keep=5))
assert truncate("", keep=3) == ""
text = "\n".join(str(i) for i in range(10))
got = truncate(text, keep=4)
lines = got.splitlines()
assert lines[:4] == ["0", "1", "2", "3"], lines
assert lines[-1] == "[truncated: 6 of 10 lines omitted]", lines[-1]
assert len(lines) == 5, lines
PYCHECK
[ $? -eq 0 ] || fail "tools/output.py does not behave as the task asked"
exit 0
