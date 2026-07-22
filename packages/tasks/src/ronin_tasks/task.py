"""Task model, state machine, and persistent store."""

from __future__ import annotations

import json
import uuid
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class TaskState(str, Enum):
    draft = "draft"
    scheduled = "scheduled"
    queued = "queued"
    running = "running"
    waiting_for_approval = "waiting_for_approval"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    blocked = "blocked"


class TaskType(str, Enum):
    one_time = "one_time"
    scheduled = "scheduled"
    recurring = "recurring"
    condition = "condition"


_ALLOWED: dict[TaskState, frozenset[TaskState]] = {
    TaskState.draft: frozenset({TaskState.scheduled, TaskState.queued, TaskState.cancelled}),
    TaskState.scheduled: frozenset({TaskState.queued, TaskState.cancelled, TaskState.blocked}),
    TaskState.queued: frozenset(
        {TaskState.running, TaskState.waiting_for_approval, TaskState.cancelled, TaskState.blocked}
    ),
    TaskState.waiting_for_approval: frozenset(
        {TaskState.running, TaskState.cancelled, TaskState.paused, TaskState.blocked}
    ),
    TaskState.running: frozenset(
        {TaskState.completed, TaskState.failed, TaskState.paused,
         TaskState.cancelled, TaskState.waiting_for_approval}
    ),
    TaskState.paused: frozenset({TaskState.running, TaskState.queued, TaskState.cancelled}),
    TaskState.failed: frozenset({TaskState.queued}),  # retry
    TaskState.blocked: frozenset({TaskState.queued, TaskState.cancelled}),
    TaskState.completed: frozenset({TaskState.queued}),  # recurring re-arm
    TaskState.cancelled: frozenset(),
}


class TransitionError(RuntimeError):
    pass


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: "task_" + uuid.uuid4().hex[:12])
    owner: str
    title: str = Field(min_length=1)
    task_type: TaskType = TaskType.one_time
    state: TaskState = TaskState.draft
    high_risk: bool = False  # must pass through approval before running
    approved_by: str = ""
    run_at: str = ""          # ISO due time for scheduled tasks
    interval_seconds: int | None = None  # recurring cadence
    history: list[dict] = Field(default_factory=list)  # [{state, at, note}]
    last_error: str = ""

    def _record(self, state: TaskState, at: str, note: str) -> None:
        self.history.append({"state": state.value, "at": at, "note": note})


class TaskStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._tasks: dict[str, Task] = {}
        if self._path.exists():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._tasks = {tid: Task.model_validate(t) for tid, t in data.items()}

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({tid: t.model_dump(mode="json") for tid, t in self._tasks.items()},
                       indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def create(self, task: Task) -> Task:
        self._tasks[task.id] = task
        self._persist()
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list_for(self, owner: str, *, state: TaskState | None = None) -> list[Task]:
        out = [t for t in self._tasks.values() if t.owner == owner]
        if state is not None:
            out = [t for t in out if t.state == state]
        return sorted(out, key=lambda t: t.id)

    def transition(self, task_id: str, new_state: TaskState, *, at: str, note: str = "",
                   approved_by: str = "") -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"unknown task {task_id!r}")
        if new_state not in _ALLOWED[task.state]:
            raise TransitionError(
                f"task {task_id}: {task.state.value} -> {new_state.value} illegal "
                f"(allowed: {sorted(s.value for s in _ALLOWED[task.state])})"
            )
        # The core safety rule: a high-risk task can only enter `running`
        # straight from `waiting_for_approval` WITH a recorded approver.
        if new_state is TaskState.running and task.high_risk:
            from_approval = task.state is TaskState.waiting_for_approval
            approver = approved_by or task.approved_by
            if not (from_approval and approver):
                raise TransitionError(
                    f"high-risk task {task_id} cannot run without passing through "
                    "waiting_for_approval and a recorded approver"
                )
            task.approved_by = approver
        task.state = new_state
        if new_state is TaskState.failed:
            task.last_error = note
        task._record(new_state, at, note)
        self._persist()
        return task
