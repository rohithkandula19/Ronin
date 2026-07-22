"""Job model and state machine for the durable job queue."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class JobState(str, Enum):
    """Lifecycle states for a job."""

    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    dead_letter = "dead_letter"
    canceled = "canceled"


#: Terminal states a job can never leave on its own.
TERMINAL_STATES: frozenset[JobState] = frozenset(
    {JobState.succeeded, JobState.canceled}
)


class Job(BaseModel):
    """A unit of durable work.

    All timestamps are caller-supplied (see the ``now`` arguments across the
    queue and worker); nothing here reads the wall clock.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    payload: dict = Field(default_factory=dict)
    state: JobState = JobState.queued
    attempts: int = 0
    max_attempts: int = 3
    next_run_at: datetime
    idempotency_key: str | None = None
    created_at: datetime
    updated_at: datetime
    #: Redacted, safe-to-log summary of the most recent failure (or ``None``).
    last_error: str | None = None
    #: Stable error code of the most recent failure (or ``None``).
    last_error_code: str | None = None

    def is_runnable(self, now: datetime) -> bool:
        """Return whether this job may be claimed at ``now``."""

        return self.state is JobState.queued and self.next_run_at <= now


__all__ = ["Job", "JobState", "TERMINAL_STATES"]
