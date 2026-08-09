"""Retention window.

``is_expired`` takes an age in seconds: that is what ``retention.sweep``
reads out of the store, and the older "minutes" note above the function was
what made the window come out 60 times too short.
"""

from __future__ import annotations

KEEP_DAYS = 30

SECONDS_PER_DAY = 24 * 60 * 60


def is_expired(age: int) -> bool:
    """True once the record is older than the retention window."""
    return age > KEEP_DAYS * SECONDS_PER_DAY
