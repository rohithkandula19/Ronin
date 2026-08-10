"""Where jobs live. Insertion order is recorded as ``Job.seq``."""

from __future__ import annotations

from taskq.errors import UnknownJob
from taskq.model import Job


class Store:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._next_seq = 0

    def add(self, job_id: str, name: str, payload: str, priority: int = 0) -> Job:
        job = Job(
            id=job_id,
            name=name,
            payload=payload,
            seq=self._next_seq,
            priority=priority,
        )
        self._next_seq += 1
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job:
        if job_id not in self._jobs:
            raise UnknownJob("no job with id %r" % job_id)
        return self._jobs[job_id]

    def all(self) -> list[Job]:
        """Every job, in insertion order — never dict order."""
        return sorted(self._jobs.values(), key=lambda job: job.seq)
