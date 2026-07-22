"""Tests for the job queue: enqueue idempotency and claim."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ronin_jobs import InMemoryJobStore, Job, JobQueue, JobState

T0 = datetime(2026, 1, 1, 12, 0, 0)


def make_queue() -> JobQueue:
    return JobQueue(store=InMemoryJobStore())


def test_enqueue_creates_queued_job() -> None:
    q = make_queue()
    job = q.enqueue("send_email", {"to": "a@b.c"}, now=T0, id="j1")
    assert job.state is JobState.queued
    assert job.attempts == 0
    assert job.next_run_at == T0
    assert job.created_at == T0 and job.updated_at == T0


def test_enqueue_idempotent_returns_existing() -> None:
    q = make_queue()
    a = q.enqueue("t", {"n": 1}, now=T0, id="j1", idempotency_key="k")
    b = q.enqueue("t", {"n": 2}, now=T0, id="j2", idempotency_key="k")
    assert a.id == b.id
    assert b.payload == {"n": 1}  # unchanged
    assert len(q.store.list()) == 1


def test_enqueue_without_key_allows_multiple() -> None:
    q = make_queue()
    q.enqueue("t", {}, now=T0, id="j1")
    q.enqueue("t", {}, now=T0, id="j2")
    assert len(q.store.list()) == 2


def test_enqueue_rejects_bad_max_attempts() -> None:
    q = make_queue()
    with pytest.raises(ValueError):
        q.enqueue("t", {}, now=T0, id="j1", max_attempts=0)


def test_enqueue_duplicate_id_raises() -> None:
    q = make_queue()
    q.enqueue("t", {}, now=T0, id="j1")
    with pytest.raises(KeyError):
        q.enqueue("t", {}, now=T0, id="j1")


def test_claim_returns_running_job() -> None:
    q = make_queue()
    q.enqueue("t", {}, now=T0, id="j1")
    claimed = q.claim(T0)
    assert claimed is not None
    assert claimed.state is JobState.running


def test_claim_none_when_empty() -> None:
    q = make_queue()
    assert q.claim(T0) is None


def test_claim_respects_next_run_at() -> None:
    q = make_queue()
    job = q.enqueue("t", {}, now=T0, id="j1")
    job.next_run_at = T0 + timedelta(seconds=100)
    q.store.put(job)
    assert q.claim(T0) is None
    assert q.claim(T0 + timedelta(seconds=100)) is not None


def test_claim_orders_by_next_run_at() -> None:
    q = make_queue()
    q.enqueue("t", {}, now=T0 + timedelta(seconds=10), id="late")
    q.enqueue("t", {}, now=T0, id="early")
    claimed = q.claim(T0 + timedelta(seconds=100))
    assert claimed is not None and claimed.id == "early"


def test_claim_does_not_reclaim_running() -> None:
    q = make_queue()
    q.enqueue("t", {}, now=T0, id="j1")
    first = q.claim(T0)
    assert first is not None
    assert q.claim(T0) is None


def test_cancel_queued_job() -> None:
    q = make_queue()
    q.enqueue("t", {}, now=T0, id="j1")
    job = q.cancel("j1", T0)
    assert job.state is JobState.canceled
    assert q.claim(T0) is None


def test_cancel_unknown_raises() -> None:
    q = make_queue()
    with pytest.raises(KeyError):
        q.cancel("nope", T0)


def test_cancel_running_rejected() -> None:
    q = make_queue()
    q.enqueue("t", {}, now=T0, id="j1")
    q.claim(T0)
    with pytest.raises(ValueError):
        q.cancel("j1", T0)
