#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import commands.resolve as m

assert m.ALIASES.get("co") == "checkout", m.ALIASES
assert m.ALIASES.get("ci") == "commit", m.ALIASES
assert m.ALIASES.get("c") == "ci", m.ALIASES

assert m.resolve("status") == "do_status"
assert m.resolve("co") == "do_checkout"
assert m.resolve("ci") == "do_commit"
assert m.resolve("c") == "do_commit"

try:
    m.resolve("nope")
except KeyError:
    pass
else:
    raise AssertionError("an unknown name must still raise KeyError")

m.ALIASES.update({"x": "y", "y": "x"})
try:
    m.resolve("x")
except ValueError as exc:
    assert "x" in str(exc) and "y" in str(exc), str(exc)
else:
    raise AssertionError("an alias cycle must raise ValueError")
PYCHECK
[ $? -eq 0 ] || fail "the specified behaviour is missing or wrong"
exit 0
