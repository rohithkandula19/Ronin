"""Unit tests for feed token normalisation."""

from __future__ import annotations

import unittest

from feedparse.tokens import dedupe, normalise_feed


class DedupeTest(unittest.TestCase):
    def test_empty_list_stays_empty(self) -> None:
        self.assertEqual(dedupe([]), [])

    def test_plain_duplicates_collapse_to_first_occurrence(self) -> None:
        self.assertEqual(dedupe(["b", "a", "b", "c", "a"]), ["b", "a", "c"])

    def test_order_is_preserved(self) -> None:
        self.assertEqual(dedupe(["zebra", "apple", "mango"]), ["zebra", "apple", "mango"])


class NormaliseFeedTest(unittest.TestCase):
    def test_records_keep_their_other_fields(self) -> None:
        records = [{"id": 1, "title": "Hello", "tokens": ["x", "x"]}]
        self.assertEqual(normalise_feed(records), [{"id": 1, "title": "Hello", "tokens": ["x"]}])

    def test_input_records_are_not_mutated(self) -> None:
        records = [{"id": 1, "tokens": ["x", "x"]}]
        normalise_feed(records)
        self.assertEqual(records[0]["tokens"], ["x", "x"])


if __name__ == "__main__":
    unittest.main()
