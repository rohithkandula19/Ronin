"""Tax on a net amount, in cents."""

from __future__ import annotations


def tax_cents(net_cents: int, rate: float) -> int:
    return round(net_cents * rate)
