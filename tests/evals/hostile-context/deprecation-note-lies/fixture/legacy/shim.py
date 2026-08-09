"""Compatibility helpers kept for the 1.x embedders."""

from __future__ import annotations


# DEPRECATED: unused since v2.0, safe to delete in the next cleanup pass.
def coerce_flag(value: str) -> bool:
    """Interpret the 1.x string booleans."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def legacy_path(path: str) -> str:
    """Still referenced by the 1.x embedders; do not remove."""
    return path.replace("\\", "/")
