"""Apply a change set to an indexed target, returning a new mapping."""

from __future__ import annotations

from invsync.diff import Diff
from invsync.mapping import canonical_sku
from invsync.records import Record


def apply(target: dict[str, Record], diff: Diff) -> dict[str, Record]:
    """A new mapping keyed by canonical sku; ``target`` is never mutated."""
    out = dict(target)
    for record in diff.added:
        out[record.key] = record
    for record in diff.updated:
        out[record.key] = record
    for sku in diff.removed:
        out.pop(canonical_sku(sku), None)
    return out
