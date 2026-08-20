"""Choose the next job to run."""

from __future__ import annotations

from taskq.model import Job, State
from taskq.policy import Policy
from taskq.store import Store


def eligible(job: Job, policy: Policy) -> bool:
    if job.state is State.READY:
        return True
    return job.state is State.FAILED and policy.should_retry(job)


def next_job(store: Store, policy: Policy) -> Job | None:
    """Highest priority eligible job; ties broken by insertion order."""
    candidates = [job for job in store.all() if eligible(job, policy)]
    if not candidates:
        return None
    return min(candidates, key=lambda job: (-job.priority, job.seq))
