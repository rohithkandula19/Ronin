"""Tasks: state machine legality, no-silent-high-risk, scheduler, persistence."""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_tasks import (
    InProcessScheduler,
    Task,
    TaskState,
    TaskStore,
    TaskType,
    TransitionError,
)


def _store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.json")


def test_illegal_transition_rejected(tmp_path: Path):
    store = _store(tmp_path)
    t = store.create(Task(owner="a", title="t"))
    with pytest.raises(TransitionError):
        store.transition(t.id, TaskState.completed, at="t0")  # draft->completed illegal


def test_high_risk_cannot_run_without_approval(tmp_path: Path):
    store = _store(tmp_path)
    t = store.create(Task(owner="a", title="deploy", high_risk=True, state=TaskState.queued))
    with pytest.raises(TransitionError, match="waiting_for_approval and a recorded approver"):
        store.transition(t.id, TaskState.running, at="t0")


def test_high_risk_runs_after_approval(tmp_path: Path):
    store = _store(tmp_path)
    t = store.create(Task(owner="a", title="deploy", high_risk=True, state=TaskState.queued))
    store.transition(t.id, TaskState.waiting_for_approval, at="t0")
    ran = store.transition(t.id, TaskState.running, at="t1", approved_by="rohith")
    assert ran.state is TaskState.running and ran.approved_by == "rohith"


def test_scheduler_runs_low_risk_task(tmp_path: Path):
    store = _store(tmp_path)
    store.create(Task(owner="a", title="summarize", state=TaskState.queued))
    sched = InProcessScheduler(store, runner=lambda t: (True, "done"))
    sched.tick("a", now="2026-07-22T10:00:00Z")
    t = store.list_for("a")[0]
    assert t.state is TaskState.completed
    assert any(h["state"] == "running" for h in t.history)  # real transitions recorded


def test_scheduler_routes_high_risk_to_approval_never_runs_silently(tmp_path: Path):
    store = _store(tmp_path)
    ran = {"count": 0}

    def runner(t):
        ran["count"] += 1
        return (True, "ran")

    store.create(Task(owner="a", title="rm stuff", high_risk=True, state=TaskState.queued))
    sched = InProcessScheduler(store, runner=runner)
    sched.tick("a", now="2026-07-22T10:00:00Z")
    t = store.list_for("a")[0]
    assert t.state is TaskState.waiting_for_approval
    assert ran["count"] == 0  # the high-risk runner NEVER fired without approval

    sched.approve(t.id, approver="rohith", now="2026-07-22T10:05:00Z")
    t = store.get(t.id)
    assert t.state is TaskState.completed and t.approved_by == "rohith"
    assert ran["count"] == 1


def test_scheduled_task_not_due_yet(tmp_path: Path):
    store = _store(tmp_path)
    store.create(Task(owner="a", title="later", task_type=TaskType.scheduled,
                      state=TaskState.scheduled, run_at="2026-07-22T12:00:00Z"))
    sched = InProcessScheduler(store, runner=lambda t: (True, "done"))
    sched.tick("a", now="2026-07-22T09:00:00Z")  # before run_at
    assert store.list_for("a")[0].state is TaskState.scheduled
    sched.tick("a", now="2026-07-22T12:30:00Z")  # after run_at
    assert store.list_for("a")[0].state is TaskState.completed


def test_failure_records_error_and_allows_retry(tmp_path: Path):
    store = _store(tmp_path)
    store.create(Task(owner="a", title="flaky", state=TaskState.queued))
    sched = InProcessScheduler(store, runner=lambda t: (False, "boom"))
    sched.tick("a", now="t0")
    t = store.list_for("a")[0]
    assert t.state is TaskState.failed and t.last_error == "boom"
    store.transition(t.id, TaskState.queued, at="t1", note="retry")  # failed->queued allowed
    assert store.get(t.id).state is TaskState.queued


def test_cancel_is_terminal(tmp_path: Path):
    store = _store(tmp_path)
    t = store.create(Task(owner="a", title="t", state=TaskState.queued))
    store.transition(t.id, TaskState.cancelled, at="t0")
    with pytest.raises(TransitionError):
        store.transition(t.id, TaskState.running, at="t1")


def test_persistence_survives_reload(tmp_path: Path):
    store = _store(tmp_path)
    t = store.create(Task(owner="a", title="persist", high_risk=True, state=TaskState.queued))
    store.transition(t.id, TaskState.waiting_for_approval, at="t0")
    store2 = TaskStore(tmp_path / "tasks.json")
    reloaded = store2.get(t.id)
    assert reloaded.state is TaskState.waiting_for_approval
    assert reloaded.high_risk and len(reloaded.history) >= 1
