#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from call.retry import TransientError, attempt

slept = []
calls = []


def flaky(fail_times, value="ok"):
    state = {"n": 0}

    def operation():
        state["n"] += 1
        calls.append(state["n"])
        if state["n"] <= fail_times:
            raise TransientError("not yet")
        return value

    return operation


slept.clear(); calls.clear()
assert attempt(flaky(0), slept.append) == "ok"
assert slept == [], slept
assert len(calls) == 1, calls

slept.clear(); calls.clear()
assert attempt(flaky(2), slept.append) == "ok"
assert slept == [0.5, 1.0], slept
assert len(calls) == 3, calls

slept.clear(); calls.clear()
try:
    attempt(flaky(5), slept.append)
except TransientError:
    pass
else:
    raise AssertionError("a permanently transient failure must re-raise TransientError")
assert slept == [0.5, 1.0], slept
assert len(calls) == 3, calls

slept.clear(); calls.clear()


def boom():
    calls.append(1)
    raise KeyError("hard")


try:
    attempt(boom, slept.append)
except KeyError:
    pass
else:
    raise AssertionError("a non-transient exception must propagate")
assert slept == [], slept
assert len(calls) == 1, calls
PYCHECK
[ $? -eq 0 ] || fail "the specified behaviour is missing or wrong"
exit 0
