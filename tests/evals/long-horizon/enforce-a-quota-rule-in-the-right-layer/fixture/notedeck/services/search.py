"""Note search: page size comes from the config, matching is case-insensitive."""

from __future__ import annotations

from ..config import Settings
from ..domain import Note
from ..errors import ValidationError
from ..repository import NotesRepo


class SearchService:
    def __init__(self, notes: NotesRepo, settings: Settings) -> None:
        self._notes = notes
        self._settings = settings

    def search(self, user_id: str, query: str, page: int = 1) -> tuple[Note, ...]:
        if page < 1:
            raise ValidationError(f"page must be 1 or greater, got {page}")
        needle = query.strip().lower()
        if not needle:
            raise ValidationError("query must not be empty")
        hits = [
            note
            for note in self._notes.list_for_user(user_id, include_archived=True)
            if needle in note.title.lower() or needle in note.body.lower()
        ]
        size = self._settings.search_page_size
        start = (page - 1) * size
        return tuple(hits[start : start + size])
