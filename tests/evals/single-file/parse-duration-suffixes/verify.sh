#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from jobsched.duration import parse_duration

for text, want in [("30", 30), ("30s", 30), ("5m", 300), ("2h", 7200), ("0m", 0)]:
    got = parse_duration(text)
    assert got == want, "parse_duration(%r) == %r, want %r" % (text, got, want)
try:
    parse_duration("7d")
except ValueError as exc:
    assert "7d" in str(exc), str(exc)
else:
    raise AssertionError("an unknown suffix must still raise ValueError")
PYCHECK
[ $? -eq 0 ] || fail "jobsched/duration.py does not behave as the task asked"
exit 0
