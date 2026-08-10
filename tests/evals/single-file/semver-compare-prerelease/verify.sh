#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from release.version import compare

cases = [
    ("1.0.0", "1.0.1", -1),
    ("1.2.0", "1.1.9", 1),
    ("1.0.0", "1.0.0", 0),
    ("1.0.0-rc1", "1.0.0", -1),
    ("1.0.0", "1.0.0-rc1", 1),
    ("1.0.0-rc1", "1.0.0-rc2", -1),
    ("1.0.0-rc2", "1.0.0-rc1", 1),
    ("1.0.0-rc1", "1.0.0-rc1", 0),
    ("2.0.0-rc1", "1.9.9", 1),
]
for left, right, want in cases:
    got = compare(left, right)
    assert got == want, "compare(%r, %r) == %r, want %r" % (left, right, got, want)
PYCHECK
[ $? -eq 0 ] || fail "release/version.py does not behave as the task asked"
exit 0
