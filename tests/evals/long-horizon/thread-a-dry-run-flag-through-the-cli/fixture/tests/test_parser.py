"""Global flags go before the subcommand; per-command flags after it."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atticus.cli.config_file import Defaults  # noqa: E402
from atticus.cli.options import Options  # noqa: E402
from atticus.cli.parser import build_parser  # noqa: E402


class ParserTest(unittest.TestCase):
    def parse(self, argv: list[str]):
        return build_parser().parse_args(argv)

    def test_the_subcommand_is_recorded(self) -> None:
        self.assertEqual(self.parse(["inspect"]).command, "inspect")

    def test_global_flags_come_before_the_subcommand(self) -> None:
        args = self.parse(["--root", "/tmp/logs", "--now", "2024-05-01", "prune"])
        self.assertEqual(args.root, "/tmp/logs")
        self.assertEqual(args.now, "2024-05-01")

    def test_keep_days_belongs_to_prune(self) -> None:
        self.assertEqual(self.parse(["prune", "--keep-days", "3"]).keep_days, 3)
        with self.assertRaises(SystemExit):
            self.parse(["inspect", "--keep-days", "3"])

    def test_a_missing_subcommand_is_a_usage_error(self) -> None:
        with self.assertRaises(SystemExit):
            self.parse([])

    def test_options_take_the_config_default_when_a_flag_is_absent(self) -> None:
        options = Options.merge(
            self.parse(["--now", "2024-05-01", "prune"]),
            Defaults(root="/from/config", keep_days=7),
        )
        self.assertEqual(str(options.root), "/from/config")
        self.assertEqual(options.keep_days, 7)

    def test_a_flag_beats_the_config_default(self) -> None:
        options = Options.merge(
            self.parse(["--root", "/from/flag", "--now", "2024-05-01", "prune", "--keep-days", "2"]),
            Defaults(root="/from/config", keep_days=7),
        )
        self.assertEqual(str(options.root), "/from/flag")
        self.assertEqual(options.keep_days, 2)


if __name__ == "__main__":
    unittest.main()
