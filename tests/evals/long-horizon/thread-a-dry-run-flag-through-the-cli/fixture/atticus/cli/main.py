"""The run loop: parse, merge, dispatch, print."""

from __future__ import annotations

import sys

from ..core.effects import FileOps
from .config_file import Defaults
from .errors import AtticusError
from .options import Options
from .parser import build_parser
from .registry import command
from .report import Report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        defaults = Defaults.load(args.config)
        options = Options.merge(args, defaults)
    except AtticusError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    report = Report()
    ops = FileOps()
    try:
        code = command(options.command).run(options, ops, report)
    except AtticusError as exc:
        print(f"{options.command} failed: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"{options.command} failed: {exc}", file=sys.stderr)
        return 1
    rendered = report.render(verbose=options.verbose)
    if rendered:
        print(rendered)
    return code
