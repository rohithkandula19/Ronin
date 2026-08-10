"""line_count reports the shape of a text file."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspectorkit.blob import Blob  # noqa: E402
from inspectorkit.plugins.line_count import PLUGIN  # noqa: E402


class LineCountTest(unittest.TestCase):
    def test_it_counts_lines_and_blanks(self) -> None:
        facts = PLUGIN.summarize(Blob(name="x", data=b"a\n\nbb\n"))
        self.assertEqual(facts["lines"], "3")
        self.assertEqual(facts["blank_lines"], "1")

    def test_it_reports_the_longest_line(self) -> None:
        facts = PLUGIN.summarize(Blob(name="x", data=b"a\nbbbb\n"))
        self.assertEqual(facts["longest_line"], "4")

    def test_a_missing_trailing_newline_is_reported(self) -> None:
        facts = PLUGIN.summarize(Blob(name="x", data=b"a"))
        self.assertEqual(facts["ends_with_newline"], "no")


if __name__ == "__main__":
    unittest.main()
