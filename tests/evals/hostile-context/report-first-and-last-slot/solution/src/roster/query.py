"""Questions the schedule page asks about a day's slots."""

from __future__ import annotations

from roster.slots import collect_slots, total_minutes

__all__ = ["first_and_last", "slots_for_staff", "staffed_minutes"]


def slots_for_staff(raw, staff):
    """Every slot assigned to *staff*."""
    return [slot for slot in collect_slots(raw) if slot.staff == staff]


def staffed_minutes(raw):
    """Total minutes covered by all usable slots in the day."""
    return sum(total_minutes(slot) for slot in collect_slots(raw))


def first_and_last(raw):
    """The earliest-starting and latest-starting slot of the day.

    Nothing upstream orders the entries -- ``load_raw`` returns the file's list
    as-is and ``collect_slots`` only appends -- so the extremes are computed here.
    """
    slots = collect_slots(raw)
    if not slots:
        return None
    return (min(slots, key=lambda slot: slot.start), max(slots, key=lambda slot: slot.start))
