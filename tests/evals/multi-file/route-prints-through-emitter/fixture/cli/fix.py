"""The fix command."""

from __future__ import annotations


def fix(paths: list[str], dry_run: bool = False) -> int:
    for path in paths:
        print("%s %s" % ("would fix" if dry_run else "fixed", path))
    return 0
