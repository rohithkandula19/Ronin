"""The summary a human reads after a sync."""

from __future__ import annotations

from invsync.diff import Diff

DRY_RUN_NOTE = "dry run: no changes written"


def render(diff: Diff, *, dry_run: bool = False) -> list[str]:
    counts = [
        "added %d" % len(diff.added),
        "updated %d" % len(diff.updated),
        "removed %d" % len(diff.removed),
    ]
    if dry_run:
        return [DRY_RUN_NOTE, *counts]
    return counts
