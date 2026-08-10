"""Routes translate; they do not decide."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from notedeck.config import Settings  # noqa: E402
from notedeck.util.clock import FixedClock  # noqa: E402
from notedeck.web.request import Request  # noqa: E402
from notedeck.wiring import build_app, build_services  # noqa: E402


class NoteRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.services = build_services(
            Settings.load(ROOT / "config" / "app.json"), FixedClock("2024-01-01T00:00:00")
        )
        self.app = build_app(self.services)
        self.app.handle(
            Request(method="POST", path="/users", body={"email": "ada@example.com"})
        )

    def post_note(self, title: str = "Kitchen rota", user: str = "user-1"):
        return self.app.handle(
            Request(method="POST", path=f"/users/{user}/notes", body={"title": title})
        )

    def test_a_created_note_is_201(self) -> None:
        response = self.post_note()
        self.assertEqual(response.status, 201)
        self.assertEqual(response.body["title"], "Kitchen rota")

    def test_an_unknown_user_is_404(self) -> None:
        self.assertEqual(self.post_note(user="user-404").status, 404)

    def test_an_empty_title_is_422(self) -> None:
        response = self.post_note(title="   ")
        self.assertEqual(response.status, 422)
        self.assertEqual(response.body["error"], "validation_error")

    def test_an_unknown_route_is_404(self) -> None:
        response = self.app.handle(Request(method="GET", path="/nope"))
        self.assertEqual((response.status, response.body["error"]), (404, "no_route"))

    def test_health_reports_ok(self) -> None:
        self.assertEqual(self.app.handle(Request(method="GET", path="/health")).status, 200)

    def test_listing_counts_notes(self) -> None:
        self.post_note()
        self.post_note(title="Reading list")
        response = self.app.handle(Request(method="GET", path="/users/user-1/notes"))
        self.assertEqual(response.body["count"], 2)


if __name__ == "__main__":
    unittest.main()
