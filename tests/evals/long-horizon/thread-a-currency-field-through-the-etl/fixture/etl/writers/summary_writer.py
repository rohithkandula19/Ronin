"""Human-readable run summary, grouped by channel."""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Mapping

from ..util.tabular import render_table


class SummaryWriter:
    name = "summary"

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._totals: dict[str, Decimal] = {}

    def write(self, record: Mapping[str, str]) -> None:
        channel = record.get("channel", "?")
        self._counts[channel] = self._counts.get(channel, 0) + 1
        try:
            amount = Decimal(record.get("amount", "0"))
        except Exception:  # noqa: BLE001 - a summary must never fail a run
            amount = Decimal(0)
        self._totals[channel] = self._totals.get(channel, Decimal(0)) + amount

    def extend(self, records: Iterable[Mapping[str, str]]) -> None:
        for record in records:
            self.write(record)

    def render(self) -> str:
        rows = [
            [channel, str(self._counts[channel]), f"{self._totals[channel]:.2f}"]
            for channel in sorted(self._counts)
        ]
        return render_table(["channel", "orders", "amount"], rows)
