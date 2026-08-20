"""Roll several invoices into one figure."""

from __future__ import annotations

from billing import totals


def grand_total(invoices: list[list[tuple[int, int]]]) -> int:
    return sum(totals.calc_total(lines) for lines in invoices)
