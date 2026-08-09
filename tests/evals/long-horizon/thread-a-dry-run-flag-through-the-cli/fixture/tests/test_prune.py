"""prune deletes what retention says is expired, and nothing else."""

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
from atticus.commands import prune  # noqa: E402
from atticus.core.effects import FileOps  # noqa: E402

NAMES = ("app.log", "app-2024-04-01.log", "app-2024-04-20.log", "worker-2024-03-02.log")


class PruneTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for name in NAMES:
            (self.root / name).write_text("line\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def options(self, keep_days: int = 14) -> Options:
        return Options(
            command="prune",
            root=self.root,
            now=date(2024, 5, 1),
            keep_days=keep_days,
        )

    def test_expired_files_go(self) -> None:
        report = Report()
        code = prune.run(self.options(), FileOps(), report)
        self.assertEqual(code, 0)
        left = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(left, ["app-2024-04-20.log", "app.log"])

    def test_what_it_deleted_is_in_the_report(self) -> None:
        report = Report()
        prune.run(self.options(), FileOps(), report)
        self.assertEqual(len(report.performed), 2)
        self.assertTrue(all(line.startswith("delete ") for line in report.performed))

    def test_a_wide_window_deletes_nothing(self) -> None:
        prune.run(self.options(keep_days=3650), FileOps(), Report())
        self.assertEqual(len(list(self.root.iterdir())), len(NAMES))


if __name__ == "__main__":
    unittest.main()
