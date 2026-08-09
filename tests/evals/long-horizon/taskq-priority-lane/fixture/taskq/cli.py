"""Argument handling for bin/taskq."""

from __future__ import annotations

import sys

from taskq.config import parse_config
from taskq.format import status_table
from taskq.model import Job
from taskq.policy import Policy
from taskq.runner import drain
from taskq.store import Store

#: Checked in so the command has deterministic output and needs no state file.
SEED = [
    ("j1", "resize", "img-1"),
    ("j2", "resize", "img-2"),
    ("j3", "notify", "user-7"),
]

CONFIG_TEXT = "max_attempts=3\nqueue=default\n"


def build() -> tuple[Store, Policy]:
    store = Store()
    for job_id, name, payload in SEED:
        store.add(job_id, name, payload)
    return store, Policy.from_config(parse_config(CONFIG_TEXT))


def _fails_on_notify(job: Job) -> None:
    if job.name == "notify":
        raise RuntimeError("notify is down")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: taskq {status|run}", file=sys.stderr)
        return 2
    command = argv[0]
    store, policy = build()
    if command == "status":
        for line in status_table(store.all()):
            print(line)
        return 0
    if command == "run":
        runs = drain(store, policy, _fails_on_notify)
        print("ran %d job(s)" % runs)
        for line in status_table(store.all()):
            print(line)
        return 0
    print("unknown command: %s" % command, file=sys.stderr)
    return 2
