"""Note storage.

This layer stores what it is handed. It raises :class:`NotFound` for a missing id
and nothing else: the seeder, the fixtures and the (future) importer all write
through here, and a rule enforced at this depth would make those impossible.
"""

from __future__ import annotations

from ..domain import Note
from ..errors import NotFound
from .memory_store import MemoryStore


class NotesRepo:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def next_id(self) -> str:
        return self._store.note_ids.take()

    def add(self, note: Note) -> Note:
        self._store.notes[note.id] = note
        return note

    def replace(self, note: Note) -> Note:
        if note.id not in self._store.notes:
            raise NotFound(f"no note with id {note.id!r}")
        self._store.notes[note.id] = note
        return note

    def get(self, note_id: str) -> Note:
        try:
            return self._store.notes[note_id]
        except KeyError:
            raise NotFound(f"no note with id {note_id!r}") from None

    def delete(self, note_id: str) -> None:
        if self._store.notes.pop(note_id, None) is None:
            raise NotFound(f"no note with id {note_id!r}")

    def list_for_user(self, user_id: str, include_archived: bool = False) -> tuple[Note, ...]:
        return tuple(
            note
            for note in self._store.notes.values()
            if note.user_id == user_id and (include_archived or not note.archived)
        )

    def count_active_for_user(self, user_id: str) -> int:
        """How many un-archived notes a user holds. Used by the dashboard counters."""
        return sum(
            1
            for note in self._store.notes.values()
            if note.user_id == user_id and not note.archived
        )

    def count_all(self) -> int:
        return len(self._store.notes)
