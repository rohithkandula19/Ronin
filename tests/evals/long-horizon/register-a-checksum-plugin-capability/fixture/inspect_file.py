#!/usr/bin/env python3
"""Inspect one file with the enabled plugins.

    python3 inspect_file.py --path samples/notes.txt --all
    python3 inspect_file.py --path samples/notes.txt --capability summary
    python3 inspect_file.py --list
"""

from __future__ import annotations

import argparse
import sys

from inspectorkit.config import Settings
from inspectorkit.dispatch import run_all
from inspectorkit.errors import InspectorError
from inspectorkit.loader import build_registry
from inspectorkit.report import render
from inspectorkit.util.io import read_blob


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/plugins.json")
    parser.add_argument("--path")
    parser.add_argument(
        "--capability",
        action="append",
        default=[],
        help="ask only this capability; may be repeated",
    )
    parser.add_argument("--all", action="store_true", help="ask everything the report shows")
    parser.add_argument("--list", action="store_true", help="list plugins and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.load(args.config)
        registry = build_registry(settings)
        if args.list:
            for plugin in registry.plugins():
                print(f"{plugin.name:16} {list(plugin.declares)!s:34} {plugin.summary_line}")
            return 0
        if not args.path:
            print("--path is required unless --list is given", file=sys.stderr)
            return 2
        if not args.capability and not args.all:
            print("give --all or at least one --capability", file=sys.stderr)
            return 2
        order = tuple(args.capability) if args.capability else settings.report_order()
        blob = read_blob(args.path)
        results = run_all(registry, order, blob)
        print(
            render(
                blob.name,
                results,
                order=order,
                top=settings.limit("histogram_top_n", 5),
            ),
            end="",
        )
    except InspectorError as exc:
        print(f"inspection failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
