"""Unit conversion to metres."""

from __future__ import annotations

TO_METRES = {"m": 1.0, "km": 1000.0, "mi": 1609.344, "ft": 0.3048}


def to_metres(value: float, unit: str) -> float:
    """Convert ``value`` in ``unit`` to metres.

    Raises:
        ValueError: if ``unit`` is not a unit we know about.
    """
    return value * TO_METRES[unit]
