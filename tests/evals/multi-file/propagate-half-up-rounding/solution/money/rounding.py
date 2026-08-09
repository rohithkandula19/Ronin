"""Rounding policy for anything that becomes a ledger entry.

Built-in ``round`` rounds halves to even, which makes our totals differ
from the ledger's by a cent on exactly the values auditors sample.
"""

from __future__ import annotations

import math


def half_up(value: float) -> int:
    """Round half away from zero: 2.5 -> 3, -2.5 -> -3."""
    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))
