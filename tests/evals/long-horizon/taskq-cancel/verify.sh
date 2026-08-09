#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import subprocess
import sys

from taskq.errors import UnknownJob
from taskq.model import State
from taskq.policy import Policy
from taskq.runner import run_one
from taskq.scheduler import next_job
from taskq.store import Store

assert "CANCELLED" in State.__members__, "State.CANCELLED is missing"
assert State.CANCELLED.value == "cancelled", State.CANCELLED.value

store = Store()
store.add("a", "x", "1")
store.add("b", "y", "2")
job = store.cancel("a")
assert job.id == "a" and job.state is State.CANCELLED
assert store.cancel("a").state is State.CANCELLED, "cancelling twice must be a no-op"
assert next_job(store, Policy()).id == "b", "a cancelled job must not be eligible"

ran = run_one(store, Policy(), lambda j: None)
assert ran == "b", ran
assert store.get("a").state is State.CANCELLED
assert run_one(store, Policy(), lambda j: None) is None

try:
    store.cancel("b")
except ValueError:
    pass
else:
    raise AssertionError("cancelling a DONE job must raise ValueError")

try:
    store.cancel("nope")
except UnknownJob:
    pass
else:
    raise AssertionError("cancelling an unknown id must raise UnknownJob")


def run(*args):
    return subprocess.run(
        [sys.executable, "bin/taskq", *args], capture_output=True, text=True
    )


good = run("cancel", "j2")
assert good.returncode == 0, good.stderr
assert good.stdout == "cancelled j2\n", repr(good.stdout)

bad = run("cancel", "j99")
assert bad.returncode == 1, (bad.returncode, bad.stdout, bad.stderr)
assert "no such job: j99" in bad.stderr, bad.stderr

status = run("status")
assert status.returncode == 0, status.stderr
assert status.stdout.splitlines()[0] == "j1 resize ready attempts=0", status.stdout

ran_cmd = run("run")
assert ran_cmd.returncode == 0, ran_cmd.stderr
assert ran_cmd.stdout.splitlines()[0].startswith("ran "), ran_cmd.stdout
PYCHECK
[ $? -eq 0 ] || fail "the change is incomplete across the layers"
exit 0
