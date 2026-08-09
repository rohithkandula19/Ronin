#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import json
import subprocess
import sys


def run(*args):
    return subprocess.run(
        [sys.executable, "bin/hosts", *args], capture_output=True, text=True
    )


plain = run()
assert plain.returncode == 0, plain.stderr
assert plain.stdout == "web-01\nweb-02\ndb-01\n", repr(plain.stdout)

js = run("--json")
assert js.returncode == 0, js.stderr
assert json.loads(js.stdout) == {"hosts": ["web-01", "web-02", "db-01"]}, js.stdout
assert len(js.stdout.strip().splitlines()) == 1, js.stdout

bad = run("--nope")
assert bad.returncode == 2, (bad.returncode, bad.stdout, bad.stderr)
assert bad.stdout == "", repr(bad.stdout)
assert "unknown option: --nope" in bad.stderr, bad.stderr
PYCHECK
[ $? -eq 0 ] || fail "the specified behaviour is missing or wrong"
exit 0
