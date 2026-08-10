#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
import subprocess
import sys

from taskq.model import Job, State
from taskq.policy import Policy
from taskq.runner import drain, run_one
from taskq.scheduler import next_job
from taskq.store import Store

assert "DEAD" in State.__members__, "State.DEAD is missing"
assert State.DEAD.value == "dead", State.DEAD.value


def boom(job):
    raise RuntimeError("always")


store = Store()
store.add("a", "x", "1")
policy = Policy(max_attempts=3)
assert run_one(store, policy, boom) == "a"
assert store.get("a").state is State.FAILED, store.get("a").state
assert store.get("a").attempts == 1
assert run_one(store, policy, boom) == "a"
assert store.get("a").state is State.FAILED
assert run_one(store, policy, boom) == "a"
assert store.get("a").state is State.DEAD, store.get("a").state
assert store.get("a").attempts == 3, store.get("a").attempts
assert run_one(store, policy, boom) is None
assert next_job(store, policy) is None
assert [job.id for job in store.dead()] == ["a"]

ok = Store()
ok.add("b", "y", "1")
assert run_one(ok, policy, lambda job: None) == "b"
assert ok.get("b").state is State.DONE
assert ok.dead() == []

order = Store()
order.add("first", "a", "1")
order.add("second", "b", "2")
assert drain(order, Policy(max_attempts=1), boom) == 2
assert [job.id for job in order.dead()] == ["first", "second"]


def run(*args):
    return subprocess.run(
        [sys.executable, "bin/taskq", *args], capture_output=True, text=True
    )


dead = run("dead")
assert dead.returncode == 0, dead.stderr
assert dead.stdout.strip().splitlines() == ["j3 notify dead attempts=3"], repr(dead.stdout)

status = run("status")
assert status.returncode == 0, status.stderr
assert status.stdout.splitlines()[0] == "j1 resize ready attempts=0", status.stdout

ran = run("run")
assert ran.returncode == 0, ran.stderr
assert ran.stdout.splitlines()[0].startswith("ran "), ran.stdout
assert "dead" in ran.stdout, ran.stdout
PYCHECK
[ $? -eq 0 ] || fail "the change is incomplete across the layers"
exit 0
