"""Argument handling for bin/invsync."""

from __future__ import annotations

import sys

from invsync.apply import apply
from invsync.diff import compute, index
from invsync.report import render
from invsync.source_csv import parse_csv

#: Checked in so the command is deterministic and needs no network.
CURRENT_CSV = "id,title,stock\nab-1,Widget,5\nab-2,Gadget,3\nab-9,Sprocket,1\n"
INCOMING_CSV = "id,title,stock\nab-1,Widget,5\nab-2,Gadget,7\nab-3,Doohickey,2\n"


def main(argv: list[str]) -> int:
    if argv != ["sync"]:
        print("usage: invsync sync", file=sys.stderr)
        return 2
    current = parse_csv(CURRENT_CSV)
    incoming = parse_csv(INCOMING_CSV)
    diff = compute(current, incoming)
    result = apply(index(current), diff)
    for line in render(diff):
        print(line)
    print("total %d" % len(result))
    return 0
