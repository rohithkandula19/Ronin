"""Retention window.

``is_expired`` takes an age in minutes.
"""

from __future__ import annotations

KEEP_DAYS = 30  # days


def is_expired(age: int) -> bool:
    """True once the record is older than the retention window."""
    return age > KEEP_DAYS * 24 * 60
