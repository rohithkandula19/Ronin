"""Injectable persistence for jobs.

The default :class:`InMemoryJobStore` keeps everything in a dict, which is all
the queue and worker need for deterministic, network-free operation. Any object
implementing :class:`JobStore` may be substituted (e.g. a database-backed store
in production) without changing the queue or worker.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .job import Job, JobState


@runtime_checkable
class JobStore(Protocol):
    """Minimal persistence contract used by the queue and worker."""

    def add(self, job: Job) -> None:
        """Persist a new job. Implementations should reject duplicate ids."""

    def get(self, job_id: str) -> Job | None:
        """Return the job with ``job_id`` or ``None``."""

    def put(self, job: Job) -> None:
        """Persist an updated job (must already exist)."""

    def by_idempotency_key(self, key: str) -> Job | None:
        """Return the job with idempotency ``key`` or ``None``."""

    def list(self) -> list[Job]:
        """Return all jobs (order unspecified)."""

    def runnable(self, now: datetime) -> list[Job]:
        """Return queued jobs whose ``next_run_at`` is at or before ``now``."""


class InMemoryJobStore:
    """A deterministic, in-process :class:`JobStore` backed by a dict."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._by_key: dict[str, str] = {}

    def add(self, job: Job) -> None:
        if job.id in self._jobs:
            raise KeyError(f"job id already exists: {job.id}")
        self._jobs[job.id] = job
        if job.idempotency_key is not None:
            self._by_key[job.idempotency_key] = job.id

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def put(self, job: Job) -> None:
        if job.id not in self._jobs:
            raise KeyError(f"unknown job id: {job.id}")
        self._jobs[job.id] = job
        if job.idempotency_key is not None:
            self._by_key[job.idempotency_key] = job.id

    def by_idempotency_key(self, key: str) -> Job | None:
        job_id = self._by_key.get(key)
        return self._jobs.get(job_id) if job_id is not None else None

    def list(self) -> list[Job]:
        return list(self._jobs.values())

    def runnable(self, now: datetime) -> list[Job]:
        ready = [j for j in self._jobs.values() if j.is_runnable(now)]
        # Deterministic ordering: oldest scheduled first, then by id as a
        # stable tie-breaker.
        ready.sort(key=lambda j: (j.next_run_at, j.created_at, j.id))
        return ready


__all__ = ["InMemoryJobStore", "JobStore"]
