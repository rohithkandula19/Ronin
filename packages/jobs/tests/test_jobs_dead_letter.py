"""Tests for the dead-letter queue: listing and requeue."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ronin_jobs import (
    DeadLetterQueue,
    JobQueue,
    JobState,
    ValidationError,
    Worker,
)

T0 = datetime(2026, 1, 1, 12, 0, 0)


def setup() -> tuple[JobQueue, Worker, DeadLetterQueue]:
    q = JobQueue()
    w = Worker(q, base_backoff_seconds=1.0)
    dlq = DeadLetterQueue(q)
    return q, w, dlq


def _fail_handler(_p: dict) -> None:
    raise ValidationError(detail="permanently bad")


def test_list_dead_lettered() -> None:
    q, w, dlq = setup()
    w.register("t", _fail_handler)
    q.enqueue("t", {}, now=T0, id="j1")
    q.enqueue("t", {}, now=T0, id="j2")
    w.drain(T0)
    dead = dlq.list()
    assert {j.id for j in dead} == {"j1", "j2"}
    assert all(j.state is JobState.dead_letter for j in dead)


def test_list_empty() -> None:
    _, _, dlq = setup()
    assert dlq.list() == []


def test_requeue_resets_and_reruns() -> None:
    q, w, dlq = setup()
    # A handler that fails first, then we swap behaviour by using a mutable flag.
    state = {"broken": True}

    def handler(_p: dict) -> None:
        if state["broken"]:
            raise ValidationError(detail="nope")

    w2 = Worker(q, base_backoff_seconds=1.0)
    w2.register("t", handler)
    q.enqueue("t", {}, now=T0, id="j1")
    w2.run_once(T0)
    assert q.get("j1").state is JobState.dead_letter

    # Fix the bug, requeue, and re-run.
    state["broken"] = False
    t1 = T0 + timedelta(hours=1)
    job = dlq.requeue("j1", t1)
    assert job.state is JobState.queued
    assert job.attempts == 0
    assert job.next_run_at == t1
    assert job.last_error is None

    done = w2.run_once(t1)
    assert done is not None and done.state is JobState.succeeded


def test_requeue_unknown_raises() -> None:
    _, _, dlq = setup()
    with pytest.raises(KeyError):
        dlq.requeue("nope", T0)


def test_requeue_non_dead_letter_rejected() -> None:
    q, w, dlq = setup()
    w.register("t", lambda _p: None)
    q.enqueue("t", {}, now=T0, id="j1")  # still queued
    with pytest.raises(ValueError):
        dlq.requeue("j1", T0)
