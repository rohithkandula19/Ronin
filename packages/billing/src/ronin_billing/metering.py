"""Append-only usage metering.

Usage is recorded as immutable events keyed by ``(org, period, meter)`` and
made idempotent on a caller-supplied ``idempotency_key`` — replaying the same
key is a no-op, so an at-least-once producer (retries, redelivery) never
double-counts. Periods are derived deterministically from a caller-supplied
``now`` (``YYYY-MM``); nothing here reads the wall clock.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


def period_of(now: datetime) -> str:
    """Deterministic billing-period key (``YYYY-MM``) for an instant."""
    return f"{now.year:04d}-{now.month:02d}"


class UsageEvent(BaseModel):
    """A single immutable metered usage event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: str
    meter: str
    quantity: int = Field(gt=0)
    period: str
    at: str  # ISO 8601, caller-supplied for determinism
    idempotency_key: str


class UsageStore:
    """Append-only, in-memory usage store. Injectable/replaceable backend."""

    def __init__(self) -> None:
        self._events: list[UsageEvent] = []
        self._seen: set[str] = set()

    def record_usage(
        self,
        org_id: str,
        meter: str,
        quantity: int,
        *,
        now: datetime,
        idempotency_key: str,
        period: str | None = None,
    ) -> bool:
        """Append a usage event. Idempotent on ``idempotency_key``.

        Returns ``True`` if the event was appended, ``False`` if the key was
        already seen (a no-op replay). ``quantity`` must be a positive int.
        """
        if quantity <= 0:
            raise ValueError("quantity must be > 0")
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        if idempotency_key in self._seen:
            return False
        event = UsageEvent(
            org_id=org_id,
            meter=meter,
            quantity=quantity,
            period=period or period_of(now),
            at=now.isoformat(),
            idempotency_key=idempotency_key,
        )
        self._events.append(event)
        self._seen.add(idempotency_key)
        return True

    def events(self) -> list[UsageEvent]:
        """All recorded events, in append order."""
        return list(self._events)

    def total(self, org_id: str, period: str, meter: str) -> int:
        """Aggregate quantity for one ``(org, period, meter)``."""
        return sum(
            e.quantity
            for e in self._events
            if e.org_id == org_id and e.period == period and e.meter == meter
        )

    def aggregate(self, org_id: str, period: str) -> dict[str, int]:
        """Total usage per meter for an org in a billing period."""
        totals: dict[str, int] = {}
        for e in self._events:
            if e.org_id == org_id and e.period == period:
                totals[e.meter] = totals.get(e.meter, 0) + e.quantity
        return totals


__all__ = ["UsageEvent", "UsageStore", "period_of"]
