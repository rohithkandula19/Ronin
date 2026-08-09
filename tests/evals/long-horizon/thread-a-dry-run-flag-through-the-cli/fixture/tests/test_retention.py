"""Retention is a pure function of the names and the day."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atticus.core.retention import expired, retention_cutoff  # noqa: E402
from atticus.core.scan import LogFile  # noqa: E402


def dated(day: str) -> LogFile:
    return LogFile(path=Path(f"app-{day}.log"), stem="app", size=10, day=date.fromisoformat(day))


CURRENT = LogFile(path=Path("app.log"), stem="app", size=10)


class RetentionTest(unittest.TestCase):
    def test_the_cutoff_is_keep_days_back(self) -> None:
        self.assertEqual(retention_cutoff(date(2024, 5, 1), 14), date(2024, 4, 17))

    def test_files_before_the_cutoff_expire(self) -> None:
        files = (dated("2024-04-01"), dated("2024-04-20"))
        self.assertEqual(
            [f.path.name for f in expired(files, date(2024, 5, 1), 14)], ["app-2024-04-01.log"]
        )

    def test_the_cutoff_day_itself_is_kept(self) -> None:
        self.assertEqual(expired((dated("2024-04-17"),), date(2024, 5, 1), 14), ())

    def test_the_current_file_never_expires(self) -> None:
        self.assertEqual(expired((CURRENT,), date(2024, 5, 1), 0), ())


if __name__ == "__main__":
    unittest.main()
