#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from retention.policy import KEEP_DAYS, is_expired
from retention.sweep import to_purge

day = 24 * 60 * 60
assert KEEP_DAYS == 30, KEEP_DAYS
assert is_expired(29 * day) is False, "a 29 day old record must survive"
assert is_expired(30 * day) is False, "the boundary is exclusive"
assert is_expired(30 * day + 1) is True
assert is_expired(31 * day) is True
assert is_expired(30 * 60 * 60) is False, "30 hours is not 30 days"
assert to_purge([0, 29 * day, 31 * day]) == [2], to_purge([0, 29 * day, 31 * day])
PYCHECK
[ $? -eq 0 ] || fail "the reported behaviour is still wrong"
exit 0
