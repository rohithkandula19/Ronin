"""The task record, and the JSON shape we persist it in.

`to_dict`/`from_dict` are the storage contract: rows written by older versions
are still on disk, so reading has to be forgiving where writing is exact.
"""

from __future__ import annotations

from dataclasses import dataclass

PRIORITIES = ("low", "normal", "high")
DEFAULT_PRIORITY = "normal"


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    done: bool = False
    # Last, with a default, so the positional calls already in the codebase and
    # the rows already on disk both keep working.
    priority: str = DEFAULT_PRIORITY

    def to_dict(self):
        """Return the JSON-ready mapping for this task."""
        return {
            "id": self.id,
            "title": self.title,
            "done": self.done,
            "priority": self.priority,
        }


def from_dict(raw):
    """Build a Task from a stored row, tolerating keys older rows lack."""
    return Task(
        id=str(raw["id"]),
        title=str(raw["title"]),
        done=bool(raw.get("done", False)),
        priority=str(raw.get("priority", DEFAULT_PRIORITY)),
    )


def pending(tasks):
    """Return the tasks that are not done, in the order given."""
    return [task for task in tasks if not task.done]
