"""The job record and its lifecycle states."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class State(StrEnum):
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(slots=True)
class Job:
    """A unit of work. Mutable because its state and attempt count move."""

    id: str
    name: str
    payload: str
    seq: int
    state: State = State.READY
    attempts: int = 0
    priority: int = 0
