"""Note rules.

Everything a caller must not be able to skip lives here, because the seeder and
the importer call this class directly and never pass through ``notedeck.web``.
Status codes are not this layer's business: it raises domain errors.
"""

from __future__ import annotations

from ..config import Settings
from ..domain import Note
from ..errors import Conflict, NotFound, PermissionDenied, ValidationError
from ..repository import NotesRepo, UsersRepo
from ..util.clock import Clock
from ..util.text import tidy


class NotesService:
    def __init__(
        self,
        notes: NotesRepo,
        users: UsersRepo,
        settings: Settings,
        clock: Clock,
    ) -> None:
        self._notes = notes
        self._users = users
        self._settings = settings
        self._clock = clock

    def create(self, user_id: str, title: str, body: str) -> Note:
        if not self._users.exists(user_id):
            raise NotFound(f"no user with id {user_id!r}")
        clean_title = tidy(title)
        if not clean_title:
            raise ValidationError("title must not be empty")
        if len(clean_title) > self._settings.max_title_length:
            raise ValidationError(
                f"title must be at most {self._settings.max_title_length} characters, "
                f"got {len(clean_title)}"
            )
        if len(body) > self._settings.max_body_length:
            raise ValidationError(
                f"body must be at most {self._settings.max_body_length} characters, "
                f"got {len(body)}"
            )
        note = Note(
            id=self._notes.next_id(),
            user_id=user_id,
            title=clean_title,
            body=body,
            created_at=self._clock.now(),
        )
        return self._notes.add(note)

    def get(self, note_id: str, user_id: str) -> Note:
        note = self._notes.get(note_id)
        self._require_owner(note, user_id)
        return note

    def update(self, note_id: str, user_id: str, title: str | None, body: str | None) -> Note:
        note = self.get(note_id, user_id)
        if note.archived:
            raise Conflict(f"note {note_id!r} is archived and cannot be edited")
        new_title = note.title if title is None else tidy(title)
        if not new_title:
            raise ValidationError("title must not be empty")
        if len(new_title) > self._settings.max_title_length:
            raise ValidationError(
                f"title must be at most {self._settings.max_title_length} characters"
            )
        new_body = note.body if body is None else body
        if len(new_body) > self._settings.max_body_length:
            raise ValidationError(
                f"body must be at most {self._settings.max_body_length} characters"
            )
        from dataclasses import replace

        return self._notes.replace(replace(note, title=new_title, body=new_body))

    def archive(self, note_id: str, user_id: str) -> Note:
        if not self._settings.archive_enabled:
            raise Conflict("archiving is disabled")
        note = self.get(note_id, user_id)
        if note.archived:
            raise Conflict(f"note {note_id!r} is already archived")
        return self._notes.replace(note.archive())

    def list_for(self, user_id: str, include_archived: bool = False) -> tuple[Note, ...]:
        if not self._users.exists(user_id):
            raise NotFound(f"no user with id {user_id!r}")
        return self._notes.list_for_user(user_id, include_archived=include_archived)

    def active_count(self, user_id: str) -> int:
        return self._notes.count_active_for_user(user_id)

    def _require_owner(self, note: Note, user_id: str) -> None:
        if note.user_id != user_id:
            raise PermissionDenied(f"note {note.id!r} does not belong to user {user_id!r}")
