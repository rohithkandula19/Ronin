"""Ronin AI OS support — tickets, incidents, and fail-closed notifications.

Three cooperating in-memory modules with no network and no randomness:

* ``tickets`` — org-scoped support tickets with a legal-transition-only state
  machine and a timeline of events.
* ``incidents`` — incident lifecycle, chronological timelines, time-to-resolve,
  resolved-only postmortems, and status-page derivation.
* ``notifications`` — a channel protocol with an enabled in-memory channel,
  DISABLED email/SMS stubs (real delivery is BLOCKED_CREDENTIALS), and a
  redacting, fail-closed fan-out notifier.

Everything is deterministic: callers supply ids and every ``now`` timestamp.
"""

from __future__ import annotations

from .incidents import (
    ActionItem,
    Incident,
    IncidentStatus,
    IncidentUpdate,
    Postmortem,
    PostmortemError,
    Severity,
    SystemStatus,
    system_status,
)
from .incidents import TransitionError as IncidentTransitionError
from .notifications import (
    BLOCKED_CREDENTIALS,
    ChannelDisabled,
    DeliveryResult,
    EmailChannel,
    InMemoryChannel,
    NotificationChannel,
    Notifier,
    SentMessage,
    SmsChannel,
    redact,
)
from .tickets import (
    Priority,
    Ticket,
    TicketEvent,
    TicketNotFoundError,
    TicketStatus,
    TicketStore,
)
from .tickets import TransitionError as TicketTransitionError

__all__ = [
    # tickets
    "Priority",
    "Ticket",
    "TicketEvent",
    "TicketNotFoundError",
    "TicketStatus",
    "TicketStore",
    "TicketTransitionError",
    # incidents
    "ActionItem",
    "Incident",
    "IncidentStatus",
    "IncidentTransitionError",
    "IncidentUpdate",
    "Postmortem",
    "PostmortemError",
    "Severity",
    "SystemStatus",
    "system_status",
    # notifications
    "BLOCKED_CREDENTIALS",
    "ChannelDisabled",
    "DeliveryResult",
    "EmailChannel",
    "InMemoryChannel",
    "NotificationChannel",
    "Notifier",
    "SentMessage",
    "SmsChannel",
    "redact",
]
