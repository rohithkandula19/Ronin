"""What has to change for the target to match the incoming feed."""

from __future__ import annotations

from dataclasses import dataclass

from invsync.records import Record


@dataclass(frozen=True, slots=True)
class Diff:
    added: list[Record]
    updated: list[Record]
    removed: list[str]


def index(records: list[Record]) -> dict[str, Record]:
    """Records keyed by sku. A later duplicate wins."""
    return {record.sku: record for record in records}


def compute(current: list[Record], incoming: list[Record]) -> Diff:
    """The change set, with every list sorted by sku so runs are diffable."""
    have = index(current)
    want = index(incoming)
    added = [want[sku] for sku in sorted(want) if sku not in have]
    updated = [
        want[sku] for sku in sorted(want) if sku in have and want[sku] != have[sku]
    ]
    removed = [sku for sku in sorted(have) if sku not in want]
    return Diff(added=added, updated=updated, removed=removed)
