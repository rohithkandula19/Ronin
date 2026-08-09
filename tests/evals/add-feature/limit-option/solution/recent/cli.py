"""The recent command."""

from __future__ import annotations

import sys

from recent.data import ENTRIES


def main(argv: list[str]) -> int:
    limit: int | None = None
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--limit":
            if not rest:
                print("--limit needs a value", file=sys.stderr)
                return 2
            raw = rest.pop(0)
            try:
                limit = int(raw)
            except ValueError:
                print("limit must be a non-negative integer: %r" % raw, file=sys.stderr)
                return 2
            if limit < 0:
                print("limit must be a non-negative integer: %r" % raw, file=sys.stderr)
                return 2
        else:
            print("unknown option: %s" % arg, file=sys.stderr)
            return 2
    chosen = ENTRIES if limit is None else ENTRIES[:limit]
    for entry in chosen:
        print(entry)
    return 0
