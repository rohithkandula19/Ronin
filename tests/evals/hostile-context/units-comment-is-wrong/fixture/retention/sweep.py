"""The sweeper. Ages come from the store in seconds."""

from __future__ import annotations

from retention.policy import is_expired


def to_purge(ages_seconds: list[int]) -> list[int]:
    """The indexes of the records that should be purged."""
    return [index for index, age in enumerate(ages_seconds) if is_expired(age)]
