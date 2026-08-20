"""Run one job. The executor is injected so nothing here blocks."""

from __future__ import annotations

from typing import Callable

from taskq.model import Job, State
from taskq.policy import Policy
from taskq.scheduler import next_job
from taskq.store import Store

Executor = Callable[[Job], None]


def run_one(store: Store, policy: Policy, executor: Executor) -> str | None:
    """Run the next eligible job. Returns its id, or None if there was none."""
    job = next_job(store, policy)
    if job is None:
        return None
    job.state = State.RUNNING
    job.attempts += 1
    try:
        executor(job)
    except Exception:
        job.state = State.FAILED if policy.should_retry(job) else State.DEAD
        return job.id
    job.state = State.DONE
    return job.id


def drain(store: Store, policy: Policy, executor: Executor, limit: int = 100) -> int:
    """Run until nothing is eligible. Returns how many runs happened."""
    runs = 0
    while runs < limit and run_one(store, policy, executor) is not None:
        runs += 1
    return runs
