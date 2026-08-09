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
    """Records keyed by canonical sku. A later duplicate wins."""
    return {record.key: record for record in records}


def _same(left: Record, right: Record) -> bool:
    return (left.key, left.name, left.qty) == (right.key, right.name, right.qty)


def compute(current: list[Record], incoming: list[Record]) -> Diff:
    """The change set, with every list sorted by sku so runs are diffable."""
    have = index(current)
    want = index(incoming)
    added = [want[key] for key in sorted(want) if key not in have]
    updated = [
        want[key]
        for key in sorted(want)
        if key in have and not _same(want[key], have[key])
    ]
    removed = [key for key in sorted(have) if key not in want]
    return Diff(added=added, updated=updated, removed=removed)
