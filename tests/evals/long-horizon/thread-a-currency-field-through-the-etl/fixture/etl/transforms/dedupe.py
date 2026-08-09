"""Drop repeated order ids inside a single run.

The retail export occasionally replays the tail of the previous night's file.
"""

from __future__ import annotations

from .base import Record, Filter


class DedupeOrders(Filter):
    name = "dedupe"

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def keep(self, record: Record) -> bool:
        order_id = record.get("order_id", "")
        if not order_id:
            # Missing ids are the validator's problem, not ours.
            return True
        if order_id in self._seen:
            return False
        self._seen.add(order_id)
        return True

    @property
    def dropped_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._seen))
