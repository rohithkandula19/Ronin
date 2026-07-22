"""Tests for the worker: success, retries, backoff, and dead-lettering."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ronin_jobs import (
    DeadLetterQueue,
    JobQueue,
    JobState,
    ProviderError,
    ValidationError,
    Worker,
    exponential_backoff,
)

T0 = datetime(2026, 1, 1, 12, 0, 0)


def make() -> tuple[JobQueue, Worker]:
    q = JobQueue()
    w = Worker(q, base_backoff_seconds=1.0, max_backoff_seconds=100.0)
    return q, w


def test_run_once_none_when_idle() -> None:
    _, w = make()
    assert w.run_once(T0) is None


def test_success_marks_succeeded() -> None:
    q, w = make()
    seen: list[dict] = []
    w.register("t", lambda p: seen.append(p))
    q.enqueue("t", {"x": 1}, now=T0, id="j1")
    job = w.run_once(T0)
    assert job is not None
    assert job.state is JobState.succeeded
    assert job.attempts == 1
    assert seen == [{"x": 1}]


def test_non_retryable_dead_letters_immediately() -> None:
    q, w = make()

    def handler(_p: dict) -> None:
        raise ValidationError(detail="bad field")

    w.register("t", handler)
    q.enqueue("t", {}, now=T0, id="j1", max_attempts=5)
    job = w.run_once(T0)
    assert job is not None
    assert job.state is JobState.dead_letter
    assert job.attempts == 1  # no wasted retries
    assert job.last_error_code == "validation_error"


def test_retryable_reschedules_with_backoff() -> None:
    q, w = make()

    def handler(_p: dict) -> None:
        raise ProviderError(detail="upstream 503")

    w.register("t", handler)
    q.enqueue("t", {}, now=T0, id="j1", max_attempts=3)
    job = w.run_once(T0)
    assert job is not None
    assert job.state is JobState.queued
    assert job.attempts == 1
    # base * 2 ** (1-1) = 1s
    assert job.next_run_at == T0 + timedelta(seconds=1)


def test_backoff_grows_and_then_dead_letters() -> None:
    q, w = make()

    def handler(_p: dict) -> None:
        raise ProviderError()

    w.register("t", handler)
    q.enqueue("t", {}, now=T0, id="j1", max_attempts=3)

    j = w.run_once(T0)
    assert j is not None and j.next_run_at == T0 + timedelta(seconds=1)

    t1 = j.next_run_at
    j = w.run_once(t1)
    assert j is not None and j.state is JobState.queued
    # second retry: base * 2 ** (2-1) = 2s
    assert j.next_run_at == t1 + timedelta(seconds=2)

    t2 = j.next_run_at
    j = w.run_once(t2)
    assert j is not None
    assert j.attempts == 3
    assert j.state is JobState.dead_letter


def test_next_run_not_ready_is_not_claimed() -> None:
    q, w = make()
    w.register("t", lambda _p: (_ for _ in ()).throw(ProviderError()))
    q.enqueue("t", {}, now=T0, id="j1", max_attempts=3)
    w.run_once(T0)  # schedules retry at T0+1
    # Running again at the same instant finds nothing runnable.
    assert w.run_once(T0) is None


def test_unknown_error_is_capped_fail_closed() -> None:
    q = JobQueue()
    w = Worker(q, base_backoff_seconds=1.0, unknown_error_attempt_cap=2)

    def handler(_p: dict) -> None:
        raise RuntimeError("kaboom")

    w.register("t", handler)
    # max_attempts is huge; the unknown-error cap must win.
    q.enqueue("t", {}, now=T0, id="j1", max_attempts=1000)

    now = T0
    for _ in range(5):
        job = w.run_once(now)
        if job is None:
            break
        now = job.next_run_at + timedelta(seconds=1)
    final = q.get("j1")
    assert final is not None
    assert final.state is JobState.dead_letter
    assert final.attempts == 2  # capped, did not loop forever


def test_missing_handler_dead_letters() -> None:
    q, w = make()
    q.enqueue("no_such_type", {}, now=T0, id="j1")
    job = w.run_once(T0)
    assert job is not None
    assert job.state is JobState.dead_letter
    assert job.last_error_code == "no_handler"


def test_register_duplicate_rejected() -> None:
    _, w = make()
    w.register("t", lambda _p: None)
    with pytest.raises(ValueError):
        w.register("t", lambda _p: None)


def test_drain_runs_all_ready() -> None:
    q, w = make()
    w.register("t", lambda _p: None)
    for i in range(5):
        q.enqueue("t", {}, now=T0, id=f"j{i}")
    count = w.drain(T0)
    assert count == 5
    assert all(j.state is JobState.succeeded for j in q.store.list())


def test_drain_stops_at_max_steps() -> None:
    q, w = make()
    w.register("t", lambda _p: None)
    for i in range(5):
        q.enqueue("t", {}, now=T0, id=f"j{i}")
    count = w.drain(T0, max_steps=2)
    assert count == 2


def test_drain_does_not_rerun_future_retries() -> None:
    q, w = make()
    w.register("t", lambda _p: (_ for _ in ()).throw(ProviderError()))
    q.enqueue("t", {}, now=T0, id="j1", max_attempts=3)
    # Only the first attempt runs; the retry is scheduled in the future.
    count = w.drain(T0)
    assert count == 1
    assert q.get("j1").state is JobState.queued


def test_last_error_is_redacted_summary() -> None:
    q, w = make()
    w.register(
        "t",
        lambda _p: (_ for _ in ()).throw(ProviderError(detail="host secret=example-placeholder")),
    )
    q.enqueue("t", {}, now=T0, id="j1", max_attempts=1)
    job = w.run_once(T0)
    assert job is not None
    assert "example-placeholder" not in (job.last_error or "")


def test_exponential_backoff_cap() -> None:
    d = exponential_backoff(10, base_seconds=1.0, cap_seconds=30.0)
    assert d == timedelta(seconds=30.0)


def test_exponential_backoff_values() -> None:
    assert exponential_backoff(1, base_seconds=2.0, cap_seconds=1000.0) == timedelta(seconds=2.0)
    assert exponential_backoff(3, base_seconds=2.0, cap_seconds=1000.0) == timedelta(seconds=8.0)


def test_exponential_backoff_rejects_zero() -> None:
    with pytest.raises(ValueError):
        exponential_backoff(0, base_seconds=1.0, cap_seconds=10.0)


def test_worker_rejects_bad_config() -> None:
    q = JobQueue()
    with pytest.raises(ValueError):
        Worker(q, base_backoff_seconds=0)
    with pytest.raises(ValueError):
        Worker(q, unknown_error_attempt_cap=0)


def test_drain_rejects_negative_max_steps() -> None:
    _, w = make()
    with pytest.raises(ValueError):
        w.drain(T0, max_steps=-1)
