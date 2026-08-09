"""The hosts command."""

from __future__ import annotations

import json
import sys

from inventory.data import HOSTS


def main(argv: list[str]) -> int:
    as_json = False
    for arg in argv:
        if arg == "--json":
            as_json = True
        else:
            print("unknown option: %s" % arg, file=sys.stderr)
            return 2
    if as_json:
        print(json.dumps({"hosts": list(HOSTS)}))
        return 0
    for host in HOSTS:
        print(host)
    return 0
