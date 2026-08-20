"""The fix command."""

from __future__ import annotations

from cli.emit import Emitter


def fix(paths: list[str], dry_run: bool = False, *, emitter: Emitter) -> int:
    for path in paths:
        emitter.line("%s %s" % ("would fix" if dry_run else "fixed", path))
    return 0
