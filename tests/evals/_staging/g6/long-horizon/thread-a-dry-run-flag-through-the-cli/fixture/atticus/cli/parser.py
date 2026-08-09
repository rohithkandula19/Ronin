"""Argument parsing.

Flags that describe the run as a whole are global and go *before* the subcommand
(``atticus --root /var/log prune``); flags that only one subcommand understands are
added by that subcommand's module.
"""

from __future__ import annotations

import argparse

from .. import __version__
from .registry import COMMANDS

PROG = "atticus"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="log housekeeping",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    parser.add_argument(
        "--config",
        default="config/atticus.json",
        help="config file with the defaults (default: config/atticus.json)",
    )
    parser.add_argument("--root", default=None, help="directory holding the log files")
    parser.add_argument(
        "--now",
        default=None,
        help="treat this YYYY-MM-DD as today, so a run is reproducible",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="add a summary line to the report",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True
    for name, command in COMMANDS.items():
        child = subparsers.add_parser(name, help=command.help, description=command.help)
        command.add_arguments(child)
    return parser
