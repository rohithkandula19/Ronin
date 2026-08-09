"""Tax on a net amount, in cents."""

from __future__ import annotations

from money.rounding import half_up


def tax_cents(net_cents: int, rate: float) -> int:
    return half_up(net_cents * rate)
