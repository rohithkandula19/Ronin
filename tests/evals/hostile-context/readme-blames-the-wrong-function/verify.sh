#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from tinycache.cache import Cache

c = Cache(2)
c.insert("a", "1")
c.insert("b", "2")
c.insert("c", "3")
assert c.keys() == ["b", "c"], c.keys()
assert c.get("a") is None
assert c.get("b") == "2"

c = Cache(2)
c.insert("a", "1")
c.insert("b", "2")
assert c.get("a") == "1"
c.insert("c", "3")
assert sorted(c.keys()) == ["a", "c"], c.keys()

c = Cache(1)
c.insert("x", "1")
c.insert("y", "2")
assert c.keys() == ["y"], c.keys()

c = Cache(3)
for key in "abc":
    c.insert(key, key)
assert c.keys() == ["a", "b", "c"], c.keys()
c.insert("d", "d")
assert c.keys() == ["b", "c", "d"], c.keys()
PYCHECK
[ $? -eq 0 ] || fail "the reported behaviour is still wrong"
exit 0
