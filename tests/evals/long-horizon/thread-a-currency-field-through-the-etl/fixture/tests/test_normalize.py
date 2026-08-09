"""Normalize cleans values without ever rejecting a row."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.transforms import Normalize  # noqa: E402


class NormalizeTest(unittest.TestCase):
    def test_it_lowercases_the_channel(self) -> None:
        out = Normalize().apply({"channel": "PARTNER"})
        self.assertEqual(out["channel"], "partner")

    def test_it_squeezes_whitespace(self) -> None:
        out = Normalize().apply({"customer_id": "  C-77  "})
        self.assertEqual(out["customer_id"], "C-77")

    def test_it_pads_amounts_to_two_places(self) -> None:
        out = Normalize().apply({"amount": "19.9"})
        self.assertEqual(out["amount"], "19.90")

    def test_it_leaves_an_unparsable_amount_for_the_validator(self) -> None:
        out = Normalize().apply({"amount": "abc"})
        self.assertEqual(out["amount"], "abc")


if __name__ == "__main__":
    unittest.main()
