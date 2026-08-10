#!/usr/bin/env python3
"""Sum logged hours per person and print a small table.

    python tally.py data/october.json
    python tally.py data/october.json --json
"""

from __future__ import annotations

import argparse
import json
import sys


def load_entries(path):
    """Read a JSON file of {"name": ..., "hours": ...} records."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def totals(entries):
    """Return [(name, hours)] summed per person, in alphabetical order."""
    sums = {}
    for entry in entries:
        name = str(entry["name"])
        sums[name] = sums.get(name, 0.0) + float(entry["hours"])
    return sorted(sums.items())


def render_table(rows):
    """Render the per-person totals as a fixed-width table."""
    lines = [f"{'NAME':<12}{'HOURS':>8}"]
    for name, hours in rows:
        lines.append(f"{name:<12}{hours:>8.2f}")
    lines.append(f"{'TOTAL':<12}{sum(hours for _, hours in rows):>8.2f}")
    return "\n".join(lines)


def render_json(rows):
    """Render the per-person totals as a JSON array, for piping into jq."""
    return json.dumps([{"name": name, "total": hours} for name, hours in rows])


def build_parser():
    parser = argparse.ArgumentParser(description="Sum logged hours per person.")
    parser.add_argument("entries", help='path to a JSON file of {"name", "hours"} records')
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print a JSON array of {name, total} instead of the table",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    rows = totals(load_entries(args.entries))
    print(render_json(rows) if args.as_json else render_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
