"""Report the leaf values of a settings document.

Usage: python3 audit_settings.py [settings.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from flatkit.walk import flatten_values


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else Path("settings.json")
    document = json.loads(source.read_text(encoding="utf-8"))
    leaves = flatten_values(document)
    print(f"{len(leaves)} leaf value(s)")
    for leaf in leaves:
        print(f"  {leaf!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
