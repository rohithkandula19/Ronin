"""The validator reports per-row problems as values."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.config import Config  # noqa: E402
from etl.validate import Validator  # noqa: E402


def fields_with_errors(record: dict[str, str]) -> list[str]:
    validator = Validator(Config.load(ROOT / "config" / "pipeline.json"))
    return [error.field for error in validator.check(7, record)]


class ValidateTest(unittest.TestCase):
    def test_a_non_integer_quantity_is_reported(self) -> None:
        self.assertIn("quantity", fields_with_errors({"quantity": "two"}))

    def test_a_channel_outside_the_allowlist_is_reported(self) -> None:
        self.assertIn("channel", fields_with_errors({"channel": "fax"}))

    def test_a_channel_inside_the_allowlist_is_accepted(self) -> None:
        self.assertNotIn("channel", fields_with_errors({"channel": "retail"}))

    def test_an_amount_without_two_decimals_is_reported(self) -> None:
        self.assertIn("amount", fields_with_errors({"amount": "19.9"}))

    def test_a_blank_optional_field_is_not_reported(self) -> None:
        self.assertNotIn("region", fields_with_errors({"region": ""}))

    def test_the_row_number_is_carried_into_the_message(self) -> None:
        validator = Validator(Config.load(ROOT / "config" / "pipeline.json"))
        errors = validator.check(7, {"quantity": "two"})
        self.assertTrue(all(error.row_number == 7 for error in errors))
        self.assertTrue(any(error.render().startswith("row 7: ") for error in errors))


if __name__ == "__main__":
    unittest.main()
