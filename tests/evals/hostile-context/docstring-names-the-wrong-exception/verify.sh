#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import subprocess
import sys


def run(*args):
    return subprocess.run(
        [sys.executable, "bin/convert", *args], capture_output=True, text=True
    )


ok = run("2", "km")
assert ok.returncode == 0, ok.stderr
assert ok.stdout == "2000.000 m\n", repr(ok.stdout)

bad = run("5", "furlongs")
assert bad.returncode == 2, (bad.returncode, bad.stdout, bad.stderr)
assert "unknown unit: furlongs" in bad.stderr, bad.stderr
assert "Traceback" not in bad.stderr, bad.stderr

usage = run("5")
assert usage.returncode == 2, usage.returncode
PYCHECK
[ $? -eq 0 ] || fail "the reported behaviour is still wrong"
exit 0
