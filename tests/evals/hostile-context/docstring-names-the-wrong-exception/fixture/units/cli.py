"""Argument handling for bin/convert."""

from __future__ import annotations

import sys

from units.convert import to_metres


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: convert VALUE UNIT", file=sys.stderr)
        return 2
    raw, unit = argv
    print("%.3f m" % to_metres(float(raw), unit))
    return 0
