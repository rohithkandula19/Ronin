"""The durable job queue: enqueue (idempotent) and claim."""

from __future__ import annotations

from datetime import datetime

from .job import Job, JobState
from .store import InMemoryJobStore, JobStore

DEFAULT_MAX_ATTEMPTS = 3


class JobQueue:
    """A deterministic, in-process durable job queue.

    Persistence is injectable; the default is an :class:`InMemoryJobStore`.
    Nothing here reads the wall clock — every method takes an explicit ``now``.
    """

    def __init__(self, store: JobStore | None = None) -> None:
        self._store: JobStore = store if store is not None else InMemoryJobStore()

    @property
    def store(self) -> JobStore:
        """The underlying persistence store."""

        return self._store

    def enqueue(
        self,
        type: str,
        payload: dict,
        *,
        now: datetime,
        id: str,
        idempotency_key: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> Job:
        """Enqueue a job, idempotent on ``idempotency_key``.

        Args:
            type: Handler type name.
            payload: Arbitrary JSON-like dict passed to the handler.
            now: Caller-supplied timestamp used for all job timestamps and as
                the initial ``next_run_at``.
            id: Caller-supplied stable job id (no random ids in logic).
            idempotency_key: If provided and a job already exists with this
                key, that existing job is returned unchanged (no duplicate).
            max_attempts: Maximum attempts before dead-lettering.

        Returns:
            The newly created job, or the pre-existing job when the
            ``idempotency_key`` already maps to one.

        Raises:
            ValueError: If ``max_attempts`` is not positive.
            KeyError: If ``id`` already exists (and no idempotency match).
        """

        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")

        if idempotency_key is not None:
            existing = self._store.by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing

        job = Job(
            id=id,
            type=type,
            payload=dict(payload),
            state=JobState.queued,
            attempts=0,
            max_attempts=max_attempts,
            next_run_at=now,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        self._store.add(job)
        return job

    def claim(self, now: datetime) -> Job | None:
        """Claim the next runnable job, marking it ``running``.

        Returns the claimed job (already persisted as running) or ``None`` when
        nothing is runnable at ``now``. Selection is deterministic: oldest
        ``next_run_at`` first.
        """

        ready = self._store.runnable(now)
        if not ready:
            return None
        job = ready[0]
        job.state = JobState.running
        job.updated_at = now
        self._store.put(job)
        return job

    def get(self, job_id: str) -> Job | None:
        """Return the job with ``job_id`` or ``None``."""

        return self._store.get(job_id)

    def cancel(self, job_id: str, now: datetime) -> Job:
        """Cancel a queued job.

        Raises:
            KeyError: If the job does not exist.
            ValueError: If the job is not in a cancelable (``queued``) state.
        """

        job = self._store.get(job_id)
        if job is None:
            raise KeyError(f"unknown job id: {job_id}")
        if job.state is not JobState.queued:
            raise ValueError(f"cannot cancel job in state {job.state.value}")
        job.state = JobState.canceled
        job.updated_at = now
        self._store.put(job)
        return job


__all__ = ["DEFAULT_MAX_ATTEMPTS", "JobQueue"]
