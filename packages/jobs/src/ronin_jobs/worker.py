"""The synchronous, caller-driven worker with retries and dead-lettering."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from .errors import classify
from .job import Job, JobState
from .queue import JobQueue

#: A handler receives the job's payload and returns nothing on success. It
#: signals failure by raising (a :class:`RoninError` for classified failures,
#: or any exception, which is classified fail-closed).
Handler = Callable[[dict], None]


def exponential_backoff(
    attempts: int,
    *,
    base_seconds: float,
    cap_seconds: float,
) -> timedelta:
    """Deterministic exponential backoff.

    The delay after the ``attempts``-th failed attempt (1-based) is
    ``base_seconds * 2 ** (attempts - 1)``, capped at ``cap_seconds``.

    Args:
        attempts: Number of attempts made so far (>= 1).
        base_seconds: Base delay for the first retry.
        cap_seconds: Maximum delay.
    """

    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    raw = base_seconds * (2 ** (attempts - 1))
    return timedelta(seconds=min(raw, cap_seconds))


class Worker:
    """A synchronous, in-process worker driving jobs to completion.

    The worker is entirely caller-driven: :meth:`run_once` and :meth:`drain`
    advance work only when invoked, and every decision derives from the
    supplied ``now``. Retry / dead-letter decisions consult the error
    taxonomy's ``retryable`` flag.
    """

    def __init__(
        self,
        queue: JobQueue,
        *,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 3600.0,
        unknown_error_attempt_cap: int = 3,
    ) -> None:
        """Build a worker over ``queue``.

        Args:
            queue: The job queue to operate on.
            base_backoff_seconds: Base delay for the first retry.
            max_backoff_seconds: Cap on the retry delay.
            unknown_error_attempt_cap: Hard cap on attempts for failures that
                are *not* classified ``RoninError`` instances. This ensures a
                buggy handler raising plain exceptions can never loop forever,
                even if ``max_attempts`` is set very high.
        """

        if base_backoff_seconds <= 0:
            raise ValueError("base_backoff_seconds must be > 0")
        if max_backoff_seconds <= 0:
            raise ValueError("max_backoff_seconds must be > 0")
        if unknown_error_attempt_cap < 1:
            raise ValueError("unknown_error_attempt_cap must be >= 1")
        self._queue = queue
        self._handlers: dict[str, Handler] = {}
        self._base = base_backoff_seconds
        self._cap = max_backoff_seconds
        self._unknown_cap = unknown_error_attempt_cap

    def register(self, type: str, handler: Handler) -> None:
        """Register ``handler`` for jobs of ``type``.

        Raises:
            ValueError: If a handler is already registered for ``type``.
        """

        if type in self._handlers:
            raise ValueError(f"handler already registered for type: {type}")
        self._handlers[type] = handler

    def run_once(self, now: datetime) -> Job | None:
        """Claim and execute at most one runnable job.

        Returns the job after execution (in its resulting state), or ``None``
        if nothing was runnable at ``now``.
        """

        job = self._queue.claim(now)
        if job is None:
            return None
        return self._execute(job, now)

    def drain(self, now: datetime, max_steps: int = 1000) -> int:
        """Run runnable jobs until none remain (or ``max_steps`` is hit).

        All work is evaluated against a single frozen ``now``; jobs whose retry
        was scheduled into the future are therefore *not* re-run in the same
        drain. Returns the number of jobs executed.

        Raises:
            ValueError: If ``max_steps`` is negative.
        """

        if max_steps < 0:
            raise ValueError("max_steps must be >= 0")
        steps = 0
        while steps < max_steps:
            job = self.run_once(now)
            if job is None:
                break
            steps += 1
        return steps

    # -- internals -----------------------------------------------------------

    def _execute(self, job: Job, now: datetime) -> Job:
        handler = self._handlers.get(job.type)
        if handler is None:
            # No handler is an operator error, not a transient one: fail closed
            # straight to the dead-letter queue.
            return self._to_dead_letter(
                job,
                now,
                code="no_handler",
                detail=f"no handler registered for type: {job.type}",
            )

        job.attempts += 1
        try:
            handler(dict(job.payload))
        except BaseException as exc:  # noqa: BLE001 - classified below
            return self._on_failure(job, now, exc)

        job.state = JobState.succeeded
        job.updated_at = now
        job.last_error = None
        job.last_error_code = None
        self._queue.store.put(job)
        return job

    def _on_failure(self, job: Job, now: datetime, exc: BaseException) -> Job:
        error = classify(exc)
        job.last_error = error.detail or error.user_message
        job.last_error_code = error.code

        # Non-retryable errors go straight to dead-letter with no wasted retry.
        if not error.retryable:
            job.state = JobState.dead_letter
            job.updated_at = now
            self._queue.store.put(job)
            return job

        # Retryable. Determine the effective attempt ceiling: a plain
        # (non-RoninError) failure is capped fail-closed so it cannot loop
        # forever, regardless of the job's configured max_attempts. The cap
        # only tightens the ceiling for unknown failures.
        ceiling = job.max_attempts
        if not _was_ronin_error(exc):
            ceiling = min(ceiling, self._unknown_cap)

        if job.attempts >= ceiling:
            job.state = JobState.dead_letter
            job.updated_at = now
            self._queue.store.put(job)
            return job

        delay = exponential_backoff(
            job.attempts, base_seconds=self._base, cap_seconds=self._cap
        )
        job.state = JobState.queued
        job.next_run_at = now + delay
        job.updated_at = now
        self._queue.store.put(job)
        return job

    def _to_dead_letter(
        self, job: Job, now: datetime, *, code: str, detail: str
    ) -> Job:
        job.state = JobState.dead_letter
        job.last_error = detail
        job.last_error_code = code
        job.updated_at = now
        self._queue.store.put(job)
        return job


def _was_ronin_error(exc: BaseException) -> bool:
    """Whether ``exc`` is (or subclasses) a classified :class:`RoninError`."""

    from .errors import RoninError

    return isinstance(exc, RoninError)


__all__ = ["Handler", "Worker", "exponential_backoff"]
