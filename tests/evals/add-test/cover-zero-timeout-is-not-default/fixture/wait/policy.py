"""Resolve the effective timeout for a wait.

``0`` means "do not wait"; only ``None`` means "use the default". A
truthiness check conflates the two and turns a non-blocking poll into a
thirty second stall.
"""

from __future__ import annotations

DEFAULT_TIMEOUT = 30.0


def effective_timeout(requested: float | None) -> float:
    if requested is None:
        return DEFAULT_TIMEOUT
    if requested < 0:
        raise ValueError("timeout must not be negative")
    return requested
