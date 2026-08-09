"""The task record, and the JSON shape we persist it in.

`to_dict`/`from_dict` are the storage contract: rows written by older versions
are still on disk, so reading has to be forgiving where writing is exact.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    done: bool = False

    def to_dict(self):
        """Return the JSON-ready mapping for this task."""
        return {"id": self.id, "title": self.title, "done": self.done}


def from_dict(raw):
    """Build a Task from a stored row, tolerating keys older rows lack."""
    return Task(
        id=str(raw["id"]),
        title=str(raw["title"]),
        done=bool(raw.get("done", False)),
    )


def pending(tasks):
    """Return the tasks that are not done, in the order given."""
    return [task for task in tasks if not task.done]
