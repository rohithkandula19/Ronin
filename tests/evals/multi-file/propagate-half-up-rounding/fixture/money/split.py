"""Split a bill between people, in cents."""

from __future__ import annotations


def per_person(total_cents: int, people: int) -> int:
    if people <= 0:
        raise ValueError("people must be positive")
    return round(total_cents / people)
