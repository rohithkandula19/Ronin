"""Dead-letter queue inspection and requeue."""

from __future__ import annotations

from datetime import datetime

from .job import Job, JobState
from .queue import JobQueue


class DeadLetterQueue:
    """A view over dead-lettered jobs, with requeue after a fix."""

    def __init__(self, queue: JobQueue) -> None:
        self._queue = queue

    def list(self) -> list[Job]:
        """Return all dead-lettered jobs, ordered by last update then id."""

        jobs = [
            j
            for j in self._queue.store.list()
            if j.state is JobState.dead_letter
        ]
        jobs.sort(key=lambda j: (j.updated_at, j.id))
        return jobs

    def requeue(self, job_id: str, now: datetime) -> Job:
        """Requeue a dead-lettered job so it can be retried after a fix.

        The attempt counter is reset so the job gets a fresh budget, and it is
        made immediately runnable at ``now``.

        Raises:
            KeyError: If the job does not exist.
            ValueError: If the job is not in the ``dead_letter`` state.
        """

        job = self._queue.store.get(job_id)
        if job is None:
            raise KeyError(f"unknown job id: {job_id}")
        if job.state is not JobState.dead_letter:
            raise ValueError(
                f"cannot requeue job in state {job.state.value}"
            )
        job.state = JobState.queued
        job.attempts = 0
        job.next_run_at = now
        job.updated_at = now
        job.last_error = None
        job.last_error_code = None
        self._queue.store.put(job)
        return job


__all__ = ["DeadLetterQueue"]
