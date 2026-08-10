"""Render an invoice total."""

from __future__ import annotations

from billing.totals import calc_total


def invoice_total(lines: list[tuple[int, int]], tax_pct: int = 0) -> int:
    base = calc_total(lines)
    return base + base * tax_pct // 100
