"""The report renders the sections it is given, in the order it is given them."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspectorkit.dispatch import Result  # noqa: E402
from inspectorkit.report import render  # noqa: E402

RESULTS = (
    Result(plugin="line_count", capability="summary", value={"lines": "3"}),
    Result(plugin="duplicate_lines", capability="warnings", value=["2 lines repeat"]),
)


class ReportTest(unittest.TestCase):
    def test_sections_follow_the_given_order(self) -> None:
        text = render("memo.txt", RESULTS, order=("warnings", "summary"))
        self.assertLess(text.index("## warnings"), text.index("## summary"))

    def test_a_capability_outside_the_order_is_not_rendered(self) -> None:
        text = render("memo.txt", RESULTS, order=("summary",))
        self.assertNotIn("## warnings", text)

    def test_the_plugin_name_labels_its_block(self) -> None:
        text = render("memo.txt", RESULTS, order=("summary",))
        self.assertIn("line_count:", text)

    def test_the_file_name_heads_the_report(self) -> None:
        self.assertTrue(render("memo.txt", (), order=("summary",)).startswith("# memo.txt"))


if __name__ == "__main__":
    unittest.main()
