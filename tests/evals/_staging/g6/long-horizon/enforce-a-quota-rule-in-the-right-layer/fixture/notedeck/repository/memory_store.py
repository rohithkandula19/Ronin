"""In-process tables.

Insertion order is preserved and ids come from a counter, so two runs of the same
sequence of calls produce the same ids and the same listings.
"""

from __future__ import annotations

from ..domain import Note, User
from ..util.ids import Sequence


class MemoryStore:
    def __init__(self) -> None:
        self.notes: dict[str, Note] = {}
        self.users: dict[str, User] = {}
        self.note_ids = Sequence("note")
        self.user_ids = Sequence("user")

    def snapshot(self) -> dict[str, object]:
        return {
            "users": [user.as_dict() for user in self.users.values()],
            "notes": [note.as_dict() for note in self.notes.values()],
        }
