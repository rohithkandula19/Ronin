"""Report anything odd about the log tree. Never touches the disk."""

from __future__ import annotations

from ..cli.options import Options
from ..cli.report import Report
from ..core import rotations, scan
from ..core.archive import overdue
from ..core.scan import unparsable
from ..core.effects import FileOps

HELP = "flag unparsable names and rotations that look overdue"


def add_arguments(parser: object) -> None:
    parser.add_argument(  # type: ignore[attr-defined]
        "--strict",
        action="store_true",
        help="exit non-zero when anything was flagged",
    )


def run(options: Options, ops: FileOps, report: Report) -> int:
    files = scan(options.root)
    flagged = 0
    for path in unparsable(options.root):
        report.note(f"cannot read a date from {path}")
        flagged += 1
    for path in overdue(files, options.now):
        report.note(f"{path} is not empty and has not been rotated")
        flagged += 1
    # verify is a preview: it says what a rotate run would do, and does nothing.
    for source, target in rotations(files, options.now):
        report.plan(f"rotate {source} -> {target}")
    report.note(f"{flagged} thing(s) flagged")
    if flagged and getattr(options, "strict", False):
        return 1
    return 0
