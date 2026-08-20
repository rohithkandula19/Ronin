"""Exponential backoff schedule, kept pure so it is testable without sleeping."""

from __future__ import annotations


def delay_for(attempt: int, base: float = 0.5, max_delay: float = 30.0) -> float:
    """Seconds to wait before retry ``attempt`` (0 is the first retry)."""
    if attempt < 0:
        raise ValueError("attempt must not be negative")
    return min(base * (2 ** attempt), max_delay)
