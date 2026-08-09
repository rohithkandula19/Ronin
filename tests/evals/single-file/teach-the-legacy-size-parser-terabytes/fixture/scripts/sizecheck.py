"""Reject space-indented lines in the tab-indented legacy modules.

Run from the release job: `python scripts/sizecheck.py src/legacy/units.py`.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path


def offenders(path: Path) -> list[int]:
    """Line numbers (1-based) whose indentation uses spaces."""
    bad: list[int] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.lstrip("\t").startswith(" "):
            bad.append(number)
    return bad


def main(argv: Sequence[str]) -> int:
    status = 0
    for name in argv:
        path = Path(name)
        bad = offenders(path)
        if bad:
            print(f"{path}: indented with spaces on lines {bad}")
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
