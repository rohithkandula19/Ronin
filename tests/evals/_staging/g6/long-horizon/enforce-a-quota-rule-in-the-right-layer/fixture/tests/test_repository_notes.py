"""The repository stores what it is handed and decides nothing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from notedeck.domain import Note  # noqa: E402
from notedeck.errors import NotFound  # noqa: E402
from notedeck.repository import MemoryStore, NotesRepo  # noqa: E402


def note(repo: NotesRepo, user_id: str = "user-1", archived: bool = False) -> Note:
    return repo.add(
        Note(
            id=repo.next_id(),
            user_id=user_id,
            title="t",
            body="b",
            created_at="2024-01-01T00:00:00",
            archived=archived,
        )
    )


class NotesRepoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = NotesRepo(MemoryStore())

    def test_ids_are_sequential(self) -> None:
        self.assertEqual([note(self.repo).id for _ in range(3)], ["note-1", "note-2", "note-3"])

    def test_a_missing_note_is_not_found(self) -> None:
        with self.assertRaises(NotFound):
            self.repo.get("note-99")

    def test_listing_skips_archived_notes_by_default(self) -> None:
        note(self.repo)
        note(self.repo, archived=True)
        self.assertEqual(len(self.repo.list_for_user("user-1")), 1)
        self.assertEqual(len(self.repo.list_for_user("user-1", include_archived=True)), 2)

    def test_active_count_ignores_other_users(self) -> None:
        note(self.repo)
        note(self.repo, user_id="user-2")
        self.assertEqual(self.repo.count_active_for_user("user-1"), 1)


if __name__ == "__main__":
    unittest.main()
