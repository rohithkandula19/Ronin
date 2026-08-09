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
    rest = list(argv)
    if not rest or rest[0] != "sync":
        print("usage: invsync sync [--dry-run]", file=sys.stderr)
        return 2
    rest.pop(0)
    dry_run = False
    while rest:
        arg = rest.pop(0)
        if arg == "--dry-run":
            dry_run = True
        else:
            print("usage: invsync sync [--dry-run]", file=sys.stderr)
            return 2
    current = parse_csv(CURRENT_CSV)
    incoming = parse_csv(INCOMING_CSV)
    diff = compute(current, incoming)
    result = apply(index(current), diff, dry_run=dry_run)
    for line in render(diff, dry_run=dry_run):
        print(line)
    print("total %d" % len(result))
    return 0
