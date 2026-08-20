"""Human-readable sizes for the summary line of a report."""

from __future__ import annotations

_UNITS = ("B", "KB", "MB", "GB", "TB")


def human_bytes(count: int) -> str:
    """Format ``count`` bytes with a binary unit ladder (1 KB == 1024 B)."""
    value = float(count)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return "%d %s" % (int(value), unit)
            return "%.1f %s" % (value, unit)
        value /= 1024
    raise AssertionError("unreachable")
