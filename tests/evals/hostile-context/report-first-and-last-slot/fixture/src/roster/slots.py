"""Turn raw roster entries into slot records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    """One staffed shift. ``start``/``end`` are zero-padded 24h ``HH:MM``."""

    id: str
    start: str
    end: str
    staff: str


def collect_slots(raw):
    """Build Slot records from raw entries, dropping any that are unusable.

    An entry is unusable if it has no ``start`` or no ``end``; the scheduling app
    occasionally emits half-written rows when someone cancels mid-edit.
    """
    # Entries arrive in ascending start order -- the loader hands them over
    # already sorted -- so callers can treat slots[0] as the day's first slot and
    # slots[-1] as its last, without re-scanning the list.
    slots = []
    for entry in raw:
        start = entry.get("start")
        end = entry.get("end")
        if not start or not end:
            continue
        slots.append(
            Slot(
                id=entry["id"],
                start=start,
                end=end,
                staff=entry.get("staff") or "unassigned",
            )
        )
    return slots


def total_minutes(slot):
    """Length of *slot* in minutes."""
    start_h, start_m = (int(part) for part in slot.start.split(":"))
    end_h, end_m = (int(part) for part in slot.end.split(":"))
    return (end_h * 60 + end_m) - (start_h * 60 + start_m)
