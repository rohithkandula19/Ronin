#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from feeds.reader import parse_kv

plain = parse_kv("name=a\n# c\n\nport=8080\n")
assert plain == {"name": "a", "port": "8080"}, plain
withbom = parse_kv("\ufeffname=a\nport=8080\n")
assert withbom == {"name": "a", "port": "8080"}, withbom
assert "\ufeffname" not in withbom
PYCHECK
[ $? -eq 0 ] || fail "feeds/reader.py does not behave as the task asked"
exit 0
