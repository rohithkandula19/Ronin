"""Argument handling for bin/convert."""

from __future__ import annotations

import sys

from units.convert import to_metres


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: convert VALUE UNIT", file=sys.stderr)
        return 2
    raw, unit = argv
    try:
        metres = to_metres(float(raw), unit)
    except KeyError:
        print("unknown unit: %s" % unit, file=sys.stderr)
        return 2
    print("%.3f m" % metres)
    return 0
