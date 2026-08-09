"""Delete dated log files that have outlived the retention window."""

from __future__ import annotations

from ..cli.options import Options
from ..cli.report import Report
from ..core import expired, human_size, scan, total_size
from ..core.effects import FileOps

HELP = "delete dated log files older than the retention window"


def add_arguments(parser: object) -> None:
    parser.add_argument(  # type: ignore[attr-defined]
        "--keep-days",
        type=int,
        default=None,
        help="retention window in days (default: from the config file)",
    )


def run(options: Options, ops: FileOps, report: Report) -> int:
    files = scan(options.root)
    doomed = expired(files, options.now, options.keep_days)
    for log in doomed:
        if options.dry_run:
            report.plan(f"delete {log.path}")
            continue
        ops.delete(log.path)
        report.did(f"delete {log.path}")
    report.note(
        f"{len(doomed)} of {len(files)} file(s) were older than {options.keep_days} days "
        f"on {options.now.isoformat()}, freeing {human_size(total_size(doomed))}"
    )
    return 0
