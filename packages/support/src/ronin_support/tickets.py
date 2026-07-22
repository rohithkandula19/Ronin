"""Support ticket model, state machine, and org-scoped in-memory store.

Tickets are deterministic: callers supply the ``id`` and every ``now``
timestamp (ISO 8601 string). There is no randomness and no network. The
state machine only permits legal transitions; anything else raises
:class:`TransitionError`. Listing and reading are strictly org-scoped so a
caller in one org can never observe another org's tickets.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Priority(str, Enum):
    """Ticket priority, lowest to highest urgency."""

    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class TicketStatus(str, Enum):
    """Lifecycle status of a support ticket."""

    open = "open"
    pending = "pending"
    resolved = "resolved"
    closed = "closed"


# Legal status transitions. Anything not listed here is rejected.
_TICKET_TRANSITIONS: dict[TicketStatus, frozenset[TicketStatus]] = {
    TicketStatus.open: frozenset(
        {TicketStatus.pending, TicketStatus.resolved, TicketStatus.closed}
    ),
    TicketStatus.pending: frozenset(
        {TicketStatus.open, TicketStatus.resolved, TicketStatus.closed}
    ),
    TicketStatus.resolved: frozenset({TicketStatus.closed, TicketStatus.open}),
    TicketStatus.closed: frozenset({TicketStatus.open}),
}


class TransitionError(RuntimeError):
    """Raised when an illegal ticket status transition is attempted."""


class TicketEvent(BaseModel):
    """A single entry in a ticket's timeline.

    ``kind`` is one of ``created``, ``comment``, ``status_change``, or
    ``assignment``. ``at`` is the caller-supplied ISO timestamp.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    at: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    message: str = ""
    from_status: TicketStatus | None = None
    to_status: TicketStatus | None = None


class Ticket(BaseModel):
    """A support ticket owned by a single organization.

    The ``id`` and all timestamps are caller-supplied for determinism.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    org_id: str = Field(min_length=1)
    requester: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    body: str = ""
    priority: Priority = Priority.normal
    status: TicketStatus = TicketStatus.open
    assignee: str = ""
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    timeline: list[TicketEvent] = Field(default_factory=list)

    def add_comment(self, *, now: str, actor: str, message: str) -> TicketEvent:
        """Append a comment event to the timeline and bump ``updated_at``."""
        if not message:
            raise ValueError("comment message must not be empty")
        event = TicketEvent(kind="comment", at=now, actor=actor, message=message)
        self.timeline.append(event)
        self.updated_at = now
        return event

    def transition(
        self, *, now: str, actor: str, to_status: TicketStatus, message: str = ""
    ) -> TicketEvent:
        """Move the ticket to ``to_status`` if the transition is legal.

        Raises :class:`TransitionError` for any illegal transition. A no-op
        transition (same status) is also rejected as illegal.
        """
        allowed = _TICKET_TRANSITIONS[self.status]
        if to_status not in allowed:
            raise TransitionError(
                f"illegal ticket transition {self.status.value} -> {to_status.value}"
            )
        event = TicketEvent(
            kind="status_change",
            at=now,
            actor=actor,
            message=message,
            from_status=self.status,
            to_status=to_status,
        )
        self.status = to_status
        self.timeline.append(event)
        self.updated_at = now
        return event

    def assign(self, *, now: str, actor: str, assignee: str) -> TicketEvent:
        """Assign the ticket to ``assignee`` and record the change."""
        if not assignee:
            raise ValueError("assignee must not be empty")
        event = TicketEvent(
            kind="assignment", at=now, actor=actor, message=assignee
        )
        self.assignee = assignee
        self.timeline.append(event)
        self.updated_at = now
        return event


class TicketNotFoundError(LookupError):
    """Raised when a ticket cannot be found within the requested org scope."""


class TicketStore:
    """In-memory, org-scoped ticket store.

    Every read and list operation requires an ``org_id`` and never returns a
    ticket belonging to a different org (fail-closed cross-org isolation).
    """

    def __init__(self) -> None:
        self._tickets: dict[str, Ticket] = {}

    def open_ticket(
        self,
        *,
        id: str,
        org_id: str,
        requester: str,
        subject: str,
        now: str,
        body: str = "",
        priority: Priority = Priority.normal,
        assignee: str = "",
    ) -> Ticket:
        """Create and store a new ticket in the ``open`` state."""
        if id in self._tickets:
            raise ValueError(f"ticket id already exists: {id}")
        ticket = Ticket(
            id=id,
            org_id=org_id,
            requester=requester,
            subject=subject,
            body=body,
            priority=priority,
            status=TicketStatus.open,
            assignee=assignee,
            created_at=now,
            updated_at=now,
        )
        ticket.timeline.append(
            TicketEvent(kind="created", at=now, actor=requester, message=subject)
        )
        self._tickets[id] = ticket
        return ticket

    def get(self, *, org_id: str, ticket_id: str) -> Ticket:
        """Return a ticket only if it exists and belongs to ``org_id``.

        Raises :class:`TicketNotFoundError` otherwise. A ticket owned by a
        different org is reported as not found (never leaked).
        """
        ticket = self._tickets.get(ticket_id)
        if ticket is None or ticket.org_id != org_id:
            raise TicketNotFoundError(ticket_id)
        return ticket

    def list_for_org(
        self, *, org_id: str, status: TicketStatus | None = None
    ) -> list[Ticket]:
        """List tickets for a single org, optionally filtered by status."""
        result = [t for t in self._tickets.values() if t.org_id == org_id]
        if status is not None:
            result = [t for t in result if t.status == status]
        return sorted(result, key=lambda t: (t.created_at, t.id))

    def save(self, ticket: Ticket) -> None:
        """Persist a ticket obtained via :meth:`get` back into the store."""
        self._tickets[ticket.id] = ticket
