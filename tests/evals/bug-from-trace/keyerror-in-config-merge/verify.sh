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

proc = subprocess.run([sys.executable, "bin/mergecfg"], capture_output=True, text=True)
assert proc.returncode == 0, "bin/mergecfg still fails:\n" + proc.stderr
got = json.loads(proc.stdout)
want = {
    "retries": 5,
    "log": {"level": "debug", "path": "/var/log/app"},
    "region": "eu-west-1",
}
assert got == want, "%r != %r" % (got, want)

from cfgmerge.merge import merge

assert merge({}, {"a": 1}) == {"a": 1}
assert merge({"a": 1}, {}) == {"a": 1}
assert merge({"a": {"b": 1}}, {"a": {"c": 2}}) == {"a": {"b": 1, "c": 2}}
assert merge({"a": {"b": 1}}, {"a": 9}) == {"a": 9}
assert merge({"a": 1}, {"a": {"b": 2}}) == {"a": {"b": 2}}
PYCHECK
[ $? -eq 0 ] || fail "the traced failure is not fixed"
exit 0
