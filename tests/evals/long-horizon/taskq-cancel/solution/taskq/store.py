"""Where jobs live. Insertion order is recorded as ``Job.seq``."""

from __future__ import annotations

from taskq.errors import UnknownJob
from taskq.model import Job, State


class Store:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._next_seq = 0

    def add(self, job_id: str, name: str, payload: str) -> Job:
        job = Job(id=job_id, name=name, payload=payload, seq=self._next_seq)
        self._next_seq += 1
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job:
        if job_id not in self._jobs:
            raise UnknownJob("no job with id %r" % job_id)
        return self._jobs[job_id]

    def cancel(self, job_id: str) -> Job:
        """Cancel a job. Finished work cannot be cancelled after the fact."""
        job = self.get(job_id)
        if job.state is State.DONE:
            raise ValueError("job %r is already done" % job_id)
        job.state = State.CANCELLED
        return job

    def all(self) -> list[Job]:
        """Every job, in insertion order — never dict order."""
        return sorted(self._jobs.values(), key=lambda job: job.seq)
