"""Line arithmetic for an invoice."""

from __future__ import annotations


def calc_total(lines: list[tuple[int, int]]) -> int:
    """Sum of unit_cents * quantity over ``lines``."""
    return sum(unit * qty for unit, qty in lines)
