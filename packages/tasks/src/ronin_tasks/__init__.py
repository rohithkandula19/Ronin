"""Ronin AI OS tasks — state machine + in-process scheduler.

A task never silently performs a high-risk action: a task flagged high-risk
must pass through `waiting_for_approval` and be explicitly approved before it
can run. The scheduler is a deterministic in-process worker (no external infra)
that advances due tasks; a production worker can replace it behind the same
`Task`/`TaskStore` interface. Progress is real — the store records actual
state transitions with timestamps, never a fabricated percentage.
"""

from .task import Task, TaskState, TaskStore, TaskType, TransitionError
from .scheduler import InProcessScheduler

__all__ = [
    "InProcessScheduler",
    "Task",
    "TaskState",
    "TaskStore",
    "TaskType",
    "TransitionError",
]
