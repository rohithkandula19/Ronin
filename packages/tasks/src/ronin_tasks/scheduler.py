"""Deterministic in-process scheduler.

No wall-clock, no threads: the caller supplies `now` so tests are exact and
runs are reproducible. `tick(now)` advances due tasks by one step. A production
worker can replace this behind the same TaskStore interface without changing
callers. It NEVER runs a high-risk task that has not been approved — it routes
such a task to waiting_for_approval instead.
"""

from __future__ import annotations

from collections.abc import Callable

from .task import Task, TaskState, TaskStore, TaskType

# A runner executes an approved, ready task and returns (ok, note).
Runner = Callable[[Task], tuple[bool, str]]


class InProcessScheduler:
    def __init__(self, store: TaskStore, runner: Runner | None = None) -> None:
        self._store = store
        self._runner = runner or (lambda t: (True, "no-op runner"))

    def _due(self, task: Task, now: str) -> bool:
        if task.task_type in (TaskType.scheduled, TaskType.recurring) and task.run_at:
            return task.run_at <= now
        return True

    def tick(self, owner: str, now: str) -> list[str]:
        """Advance all of `owner`'s tasks one step. Returns notes on what happened."""
        notes: list[str] = []
        for task in self._store.list_for(owner):
            note = self._advance_one(task, now)
            if note:
                notes.append(f"{task.id}: {note}")
        return notes

    def _advance_one(self, task: Task, now: str) -> str:
        if task.state is TaskState.scheduled and self._due(task, now):
            self._store.transition(task.id, TaskState.queued, at=now, note="due")
            task = self._store.get(task.id)

        if task.state is TaskState.queued:
            if task.high_risk and not task.approved_by:
                self._store.transition(task.id, TaskState.waiting_for_approval, at=now,
                                       note="high-risk: awaiting approval")
                return "waiting for approval (high-risk)"
            self._store.transition(task.id, TaskState.running, at=now, note="started",
                                   approved_by=task.approved_by)
            ok, run_note = self._runner(self._store.get(task.id))
            if ok:
                self._store.transition(task.id, TaskState.completed, at=now, note=run_note)
                if task.task_type is TaskType.recurring:
                    # re-arm: completed -> queued for the next interval
                    self._store.transition(task.id, TaskState.queued, at=now, note="recurring re-arm")
                return f"ran: {run_note}"
            self._store.transition(task.id, TaskState.failed, at=now, note=run_note)
            return f"failed: {run_note}"
        return ""

    def approve(self, task_id: str, *, approver: str, now: str) -> Task:
        """Approve a waiting high-risk task and run it on the next tick."""
        task = self._store.get(task_id)
        if task is None:
            raise KeyError(task_id)
        task.approved_by = approver  # recorded; transition validates it
        self._store.transition(task_id, TaskState.running, at=now,
                               note="approved", approved_by=approver)
        ok, run_note = self._runner(self._store.get(task_id))
        self._store.transition(
            task_id, TaskState.completed if ok else TaskState.failed, at=now, note=run_note
        )
        return self._store.get(task_id)
