"""Ronin AI OS jobs — a durable, deterministic in-process job queue.

The queue is fully caller-driven and network-free: enqueue jobs, then advance
them with a synchronous :class:`Worker` whose retry and dead-letter decisions
are governed by an honest error taxonomy. All time is caller-supplied (every
mutating call takes ``now``); there are no background threads, timers, or wall
-clock reads, so runs are deterministic and reproducible.

Retryable failures are retried with deterministic exponential backoff up to a
job's ``max_attempts`` and then dead-lettered; non-retryable failures fail
straight to the dead-letter queue with no wasted retries. Failures that are not
classified errors are treated as retryable but capped fail-closed so a buggy
handler can never loop forever. User-facing messages are always safe — raw
exception detail is redacted and never surfaced to callers.
"""

from .dead_letter import DeadLetterQueue
from .errors import (
    PaymentsDisabledError,
    PermissionError,
    ProviderError,
    RateLimitedError,
    RoninError,
    Severity,
    SystemError,
    TimeoutError,
    UserError,
    ValidationError,
    classify,
    is_retryable,
    redact,
)
from .job import TERMINAL_STATES, Job, JobState
from .queue import DEFAULT_MAX_ATTEMPTS, JobQueue
from .store import InMemoryJobStore, JobStore
from .worker import Handler, Worker, exponential_backoff

__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "TERMINAL_STATES",
    "DeadLetterQueue",
    "Handler",
    "InMemoryJobStore",
    "Job",
    "JobQueue",
    "JobState",
    "JobStore",
    "PaymentsDisabledError",
    "PermissionError",
    "ProviderError",
    "RateLimitedError",
    "RoninError",
    "Severity",
    "SystemError",
    "TimeoutError",
    "UserError",
    "ValidationError",
    "Worker",
    "classify",
    "exponential_backoff",
    "is_retryable",
    "redact",
]
