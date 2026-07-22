"""Subscription lifecycle state machine.

States: ``trialing -> active -> past_due -> canceled`` with ``suspended`` as a
reachable hold state. Only legal transitions are permitted; anything else
raises :class:`IllegalTransition`. ``canceled`` is terminal. Period boundaries
are driven by a caller-supplied ``now`` — no wall-clock reads.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict

from .errors import IllegalTransition


class SubscriptionState(str, Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    CANCELED = "canceled"


#: Legal transitions. ``canceled`` is absent as a source key => terminal.
_TRANSITIONS: dict[SubscriptionState, frozenset[SubscriptionState]] = {
    SubscriptionState.TRIALING: frozenset(
        {SubscriptionState.ACTIVE, SubscriptionState.CANCELED, SubscriptionState.SUSPENDED}
    ),
    SubscriptionState.ACTIVE: frozenset(
        {SubscriptionState.PAST_DUE, SubscriptionState.CANCELED, SubscriptionState.SUSPENDED}
    ),
    SubscriptionState.PAST_DUE: frozenset(
        {SubscriptionState.ACTIVE, SubscriptionState.CANCELED, SubscriptionState.SUSPENDED}
    ),
    SubscriptionState.SUSPENDED: frozenset(
        {SubscriptionState.ACTIVE, SubscriptionState.CANCELED}
    ),
    SubscriptionState.CANCELED: frozenset(),
}


def can_transition(src: SubscriptionState, dst: SubscriptionState) -> bool:
    """True iff ``src -> dst`` is a legal transition."""
    return dst in _TRANSITIONS.get(src, frozenset())


class Subscription(BaseModel):
    """A subscription's state plus its current billing-period window."""

    model_config = ConfigDict(extra="forbid")

    org_id: str
    plan_slug: str
    state: SubscriptionState = SubscriptionState.TRIALING
    current_period_start: str  # ISO, caller-supplied
    current_period_end: str  # ISO, caller-supplied
    updated_at: str  # ISO, caller-supplied

    def transition(self, dst: SubscriptionState, *, now: datetime) -> SubscriptionState:
        """Move to ``dst`` if legal, stamping ``updated_at`` with ``now``.

        Raises :class:`IllegalTransition` otherwise, leaving state unchanged.
        """
        if not can_transition(self.state, dst):
            raise IllegalTransition(
                f"cannot move subscription from {self.state.value!r} to {dst.value!r}"
            )
        self.state = dst
        self.updated_at = now.isoformat()
        return self.state

    def is_period_active(self, now: datetime) -> bool:
        """True iff ``now`` falls within ``[current_period_start, current_period_end)``."""
        return self.current_period_start <= now.isoformat() < self.current_period_end

    def renew_period(self, *, start: datetime, end: datetime, now: datetime) -> None:
        """Roll the billing-period window forward (caller supplies the bounds)."""
        if end <= start:
            raise ValueError("period end must be after start")
        self.current_period_start = start.isoformat()
        self.current_period_end = end.isoformat()
        self.updated_at = now.isoformat()


__all__ = ["Subscription", "SubscriptionState", "can_transition"]
