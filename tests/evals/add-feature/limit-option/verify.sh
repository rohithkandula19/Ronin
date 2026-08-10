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
        [sys.executable, "bin/recent", *args], capture_output=True, text=True
    )


everything = run()
assert everything.returncode == 0, everything.stderr
assert everything.stdout == "alpha\nbeta\ngamma\ndelta\n", repr(everything.stdout)

two = run("--limit", "2")
assert two.returncode == 0, two.stderr
assert two.stdout == "alpha\nbeta\n", repr(two.stdout)

zero = run("--limit", "0")
assert zero.returncode == 0, zero.stderr
assert zero.stdout == "", repr(zero.stdout)

for args in (("--limit", "-1"), ("--limit", "x"), ("--limit",)):
    bad = run(*args)
    assert bad.returncode == 2, (args, bad.returncode, bad.stderr)
    assert "limit" in bad.stderr, (args, bad.stderr)
PYCHECK
[ $? -eq 0 ] || fail "the specified behaviour is missing or wrong"
exit 0
