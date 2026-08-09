"""Compatibility helpers kept for the 1.x embedders."""

from __future__ import annotations


# Called from app.boot; the "unused since v2.0" note above it was wrong.
def coerce_flag(value: str) -> bool:
    """Interpret the 1.x string booleans."""
    return value.strip().lower() in {"1", "true", "yes", "on"}
