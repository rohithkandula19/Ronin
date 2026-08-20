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
        [sys.executable, "bin/tree", *args], capture_output=True, text=True
    )


one = run("--depth", "1")
assert one.returncode == 0, one.stderr
assert one.stdout == "README.md\nsrc\n", repr(one.stdout)

two = run("--depth", "2")
assert two.returncode == 0, two.stderr
assert two.stdout == "README.md\nsrc\n  app.py\n  lib\n", repr(two.stdout)

everything = run()
assert everything.returncode == 0, everything.stderr
assert everything.stdout == (
    "README.md\nsrc\n  app.py\n  lib\n    util.py\n"
), repr(everything.stdout)
PYCHECK
[ $? -eq 0 ] || fail "the reported behaviour is still wrong"
exit 0
