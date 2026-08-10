#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from report.units import human_bytes

cases = [
    (0, "0 B"),
    (512, "512 B"),
    (1023, "1023 B"),
    (1024, "1.0 KB"),
    (1536, "1.5 KB"),
    (1024 ** 2, "1.0 MB"),
    (1024 ** 3, "1.0 GB"),
    (1024 ** 4, "1.0 TB"),
    (5 * 1024 ** 4, "5.0 TB"),
]
for count, want in cases:
    got = human_bytes(count)
    assert got == want, "human_bytes(%d) == %r, want %r" % (count, got, want)
PYCHECK
[ $? -eq 0 ] || fail "report/units.py does not behave as the task asked"
exit 0
