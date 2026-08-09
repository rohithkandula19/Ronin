"""Apply a change set to an indexed target, returning a new mapping."""

from __future__ import annotations

from invsync.diff import Diff
from invsync.records import Record


def apply(
    target: dict[str, Record], diff: Diff, *, dry_run: bool = False
) -> dict[str, Record]:
    """A new mapping; ``target`` is never mutated."""
    out = dict(target)
    if dry_run:
        return out
    for record in diff.added:
        out[record.sku] = record
    for record in diff.updated:
        out[record.sku] = record
    for sku in diff.removed:
        out.pop(sku, None)
    return out
