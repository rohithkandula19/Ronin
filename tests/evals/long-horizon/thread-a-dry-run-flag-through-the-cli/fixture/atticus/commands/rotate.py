"""Rotate the current log file into today's dated file and start a new one."""

from __future__ import annotations

from ..cli.options import Options
from ..cli.report import Report
from ..core import rotations, scan
from ..core.effects import FileOps

HELP = "rotate <name>.log into <name>-<today>.log and start a fresh one"


def add_arguments(parser: object) -> None:
    parser.add_argument(  # type: ignore[attr-defined]
        "--keep-current",
        action="store_true",
        help="leave the current file in place instead of starting an empty one",
    )


def run(options: Options, ops: FileOps, report: Report) -> int:
    files = scan(options.root)
    pending = rotations(files, options.now)
    for source, target in pending:
        ops.move(source, target)
        report.did(f"rotate {source} -> {target}")
        ops.create_empty(source)
        report.did(f"create {source}")
    report.note(
        f"{len(pending)} current file(s) rotated for {options.now.isoformat()}"
    )
    return 0
