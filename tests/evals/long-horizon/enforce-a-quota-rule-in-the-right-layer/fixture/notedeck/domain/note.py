"""The note record."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..util.text import excerpt


@dataclass(frozen=True)
class Note:
    id: str
    user_id: str
    title: str
    body: str
    created_at: str
    archived: bool = False

    def archive(self) -> "Note":
        return replace(self, archived=True)

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "body": self.body,
            "created_at": self.created_at,
            "archived": self.archived,
        }

    def as_summary(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "excerpt": excerpt(self.body),
            "archived": self.archived,
        }
