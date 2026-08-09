"""Render the pending release notes.

Usage: python3 make_changelog.py [--explain]
"""

from __future__ import annotations

import sys
from pathlib import Path

from chg.config import describe_defaults
from chg.pipeline import build_changelog


def main(argv: list[str]) -> int:
    if "--explain" in argv:
        for line in describe_defaults():
            print(line)
        return 0
    print(build_changelog(Path("entries.json"), Path("chg.toml")), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
