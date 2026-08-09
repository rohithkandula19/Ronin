#!/usr/bin/env bash
# Runs inside the temp workspace after the agent finishes. Exit 0 = solved.
# Stdlib python + POSIX shell only; no network, no clock, no randomness.
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
"$PY" - <<'PYCHECK'
from taskq.config import KNOWN_KEYS, parse_config
from taskq.errors import ConfigError
from taskq.format import status_line, status_table
from taskq.model import Job, State
from taskq.policy import Policy
from taskq.scheduler import next_job
from taskq.store import Store

job = Job(id="j", name="n", payload="p", seq=0)
assert job.priority == 0, "Job.priority must default to 0"

store = Store()
store.add("low", "a", "1")
store.add("high", "b", "2", priority=5)
store.add("mid", "c", "3", priority=1)
assert store.get("high").priority == 5
assert next_job(store, Policy()).id == "high"
store.get("high").state = State.DONE
assert next_job(store, Policy()).id == "mid"
store.get("mid").state = State.DONE
assert next_job(store, Policy()).id == "low"
store.get("low").state = State.DONE
assert next_job(store, Policy()) is None

ties = Store()
ties.add("first", "a", "1", priority=2)
ties.add("second", "b", "2", priority=2)
assert next_job(ties, Policy()).id == "first"

assert status_line(Job(id="j1", name="resize", payload="x", seq=0)) == (
    "j1 resize ready attempts=0 prio=0"
), status_line(Job(id="j1", name="resize", payload="x", seq=0))
assert status_line(Job(id="j2", name="n", payload="x", seq=1, priority=7)).endswith("prio=7")
assert len(status_table([Job(id="a", name="b", payload="c", seq=0)])) == 1

assert "default_priority" in KNOWN_KEYS
assert parse_config("")["default_priority"] == "0"
assert parse_config("default_priority=4\n")["default_priority"] == "4"
assert Policy.from_config(parse_config("")).default_priority == 0
assert Policy.from_config(parse_config("default_priority=9\n")).default_priority == 9
try:
    Policy.from_config(parse_config("default_priority=abc\n"))
except ConfigError:
    pass
else:
    raise AssertionError("a non-integer default_priority must raise ConfigError")

# The existing scheduler tests must still pass.
plain = Store()
plain.add("a", "x", "1")
plain.add("b", "y", "2")
assert next_job(plain, Policy()).id == "a"
assert next_job(Store(), Policy()) is None
PYCHECK
[ $? -eq 0 ] || fail "the change is incomplete across the layers"
exit 0
