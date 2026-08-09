"""List the log files found under the root. Never touches the disk."""

from __future__ import annotations

from ..cli.options import Options
from ..cli.report import Report
from ..core import current_files, dated_files, human_size, scan, total_size
from ..core.effects import FileOps

HELP = "list the log files found, with sizes"


def add_arguments(parser: object) -> None:
    parser.add_argument(  # type: ignore[attr-defined]
        "--stem",
        default=None,
        help="only show files whose name starts with this stem",
    )


def run(options: Options, ops: FileOps, report: Report) -> int:
    files = scan(options.root)
    for log in files:
        label = "current" if log.is_current else log.day.isoformat()  # type: ignore[union-attr]
        report.note(f"{log.path}  {label}  {human_size(log.size)}")
    report.note(
        f"{len(dated_files(files))} dated and {len(current_files(files))} current file(s), "
        f"{human_size(total_size(files))} in total"
    )
    return 0
