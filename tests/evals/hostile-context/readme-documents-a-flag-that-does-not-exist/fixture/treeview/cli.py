"""Argument handling for bin/tree."""

from __future__ import annotations

import sys

from treeview.walk import TREE, lines


def main(argv: list[str]) -> int:
    depth: int | None = None
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--depth":
            if not rest:
                print("--depth needs a value", file=sys.stderr)
                return 2
            depth = int(rest.pop(0)) - 1
        else:
            print("unknown option: %s" % arg, file=sys.stderr)
            return 2
    for line in lines(TREE, depth):
        print(line)
    return 0
