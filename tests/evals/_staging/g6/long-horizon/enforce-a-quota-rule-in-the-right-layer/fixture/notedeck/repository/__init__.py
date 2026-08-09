"""Storage. Dumb on purpose: no rules live here."""

from __future__ import annotations

from .memory_store import MemoryStore
from .notes_repo import NotesRepo
from .users_repo import UsersRepo

__all__ = ["MemoryStore", "NotesRepo", "UsersRepo"]
