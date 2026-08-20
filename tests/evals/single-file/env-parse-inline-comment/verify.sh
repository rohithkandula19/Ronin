#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from conf.envfile import parse_env

got = parse_env(
    "# leading comment\n"
    "TOKEN=abc#123\n"
    "NAME=bob   # trailing comment\n"
    'MSG="hash # inside"\n'
    "EMPTY=\n"
    "SQ='a # b'\n"
)
want = {
    "TOKEN": "abc#123",
    "NAME": "bob",
    "MSG": "hash # inside",
    "EMPTY": "",
    "SQ": "a # b",
}
assert got == want, got
PYCHECK
[ $? -eq 0 ] || fail "conf/envfile.py does not behave as the task asked"
exit 0
