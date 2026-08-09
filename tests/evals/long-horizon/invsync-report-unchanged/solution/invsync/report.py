"""The summary a human reads after a sync."""

from __future__ import annotations

from invsync.diff import Diff


def render(diff: Diff) -> list[str]:
    return [
        "added %d" % len(diff.added),
        "updated %d" % len(diff.updated),
        "unchanged %d" % len(diff.unchanged),
        "removed %d" % len(diff.removed),
    ]
