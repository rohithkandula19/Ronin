"""Ticket ordering for the queue view."""

from __future__ import annotations


def by_priority(tickets: list[tuple[str, int]]) -> list[str]:
    """Ids ordered by descending priority, ties by ascending id."""
    return [
        ticket_id
        for ticket_id, _ in sorted(tickets, key=lambda item: (-item[1], item[0]))
    ]
