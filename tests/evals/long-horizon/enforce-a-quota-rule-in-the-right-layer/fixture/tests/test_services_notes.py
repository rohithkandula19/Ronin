"""Service rules, exercised without going anywhere near the web layer."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from notedeck.config import Settings  # noqa: E402
from notedeck.errors import Conflict, NotFound, PermissionDenied, ValidationError  # noqa: E402
from notedeck.util.clock import FixedClock  # noqa: E402
from notedeck.wiring import build_services  # noqa: E402


class NotesServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.services = build_services(
            Settings.load(ROOT / "config" / "app.json"), FixedClock("2024-01-01T00:00:00")
        )
        self.services.users.create(email="ada@example.com", display_name="Ada")

    def test_creating_a_note_tidies_the_title(self) -> None:
        note = self.services.notes.create("user-1", "  spaced   out  ", "body")
        self.assertEqual(note.title, "spaced out")
        self.assertEqual(note.created_at, "2024-01-01T00:00:00")

    def test_an_unknown_user_cannot_hold_notes(self) -> None:
        with self.assertRaises(NotFound):
            self.services.notes.create("user-404", "title", "body")

    def test_an_empty_title_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.services.notes.create("user-1", "   ", "body")

    def test_an_over_long_title_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.services.notes.create("user-1", "x" * 200, "body")

    def test_a_note_belongs_to_its_owner(self) -> None:
        note = self.services.notes.create("user-1", "mine", "body")
        self.services.users.create(email="grace@example.com", display_name="Grace")
        with self.assertRaises(PermissionDenied):
            self.services.notes.get(note.id, "user-2")

    def test_archiving_twice_is_a_conflict(self) -> None:
        note = self.services.notes.create("user-1", "mine", "body")
        self.services.notes.archive(note.id, "user-1")
        with self.assertRaises(Conflict):
            self.services.notes.archive(note.id, "user-1")

    def test_archived_notes_leave_the_active_listing(self) -> None:
        first = self.services.notes.create("user-1", "one", "body")
        self.services.notes.create("user-1", "two", "body")
        self.services.notes.archive(first.id, "user-1")
        self.assertEqual([n.title for n in self.services.notes.list_for("user-1")], ["two"])
        self.assertEqual(self.services.notes.active_count("user-1"), 1)


if __name__ == "__main__":
    unittest.main()
