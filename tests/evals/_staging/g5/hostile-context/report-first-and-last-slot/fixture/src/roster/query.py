"""Questions the schedule page asks about a day's slots."""

from __future__ import annotations

from roster.slots import collect_slots, total_minutes

__all__ = ["slots_for_staff", "staffed_minutes"]


def slots_for_staff(raw, staff):
    """Every slot assigned to *staff*."""
    return [slot for slot in collect_slots(raw) if slot.staff == staff]


def staffed_minutes(raw):
    """Total minutes covered by all usable slots in the day."""
    return sum(total_minutes(slot) for slot in collect_slots(raw))
