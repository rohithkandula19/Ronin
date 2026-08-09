"""rotate moves the current file aside and starts an empty one."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atticus.cli.options import Options  # noqa: E402
from atticus.cli.report import Report  # noqa: E402
from atticus.commands import rotate  # noqa: E402
from atticus.core.effects import FileOps  # noqa: E402


class RotateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "app.log").write_text("busy\n", encoding="utf-8")
        (self.root / "worker.log").write_text("busy\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def options(self) -> Options:
        return Options(command="rotate", root=self.root, now=date(2024, 5, 1), keep_days=14)

    def test_the_current_file_is_dated_and_replaced(self) -> None:
        rotate.run(self.options(), FileOps(), Report())
        names = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(
            names,
            [
                "app-2024-05-01.log",
                "app.log",
                "worker-2024-05-01.log",
                "worker.log",
            ],
        )
        self.assertEqual((self.root / "app.log").read_text(encoding="utf-8"), "")
        self.assertEqual((self.root / "app-2024-05-01.log").read_text(encoding="utf-8"), "busy\n")

    def test_rotating_twice_in_a_day_is_a_no_op(self) -> None:
        rotate.run(self.options(), FileOps(), Report())
        report = Report()
        rotate.run(self.options(), FileOps(), report)
        self.assertEqual(report.performed, [])


if __name__ == "__main__":
    unittest.main()
