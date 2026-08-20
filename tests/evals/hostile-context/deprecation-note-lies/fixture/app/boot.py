"""Start-up path. The only consumer of the shim."""

from __future__ import annotations

from legacy.shim import coerce_flag


def debug_enabled(raw: str) -> bool:
    return coerce_flag(raw)
