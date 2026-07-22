"""Incident model, state machine, postmortems, and status-page derivation.

Incidents are deterministic: callers supply the ``id`` and every ``now``
timestamp (ISO 8601 string). Time-to-resolve is computed from the supplied
``started_at`` / ``resolved_at`` values. A postmortem can only be attached
once the incident is resolved (fail-closed otherwise). Overall system status
is derived from the currently active incidents: the worst active severity
wins, and with no active incidents the system reads ``operational``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    """Incident severity. ``sev1`` is the most severe."""

    sev1 = "sev1"
    sev2 = "sev2"
    sev3 = "sev3"
    sev4 = "sev4"


# Lower rank == worse. Used to pick the worst active severity.
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.sev1: 1,
    Severity.sev2: 2,
    Severity.sev3: 3,
    Severity.sev4: 4,
}


class IncidentStatus(str, Enum):
    """Lifecycle status of an incident."""

    investigating = "investigating"
    identified = "identified"
    monitoring = "monitoring"
    resolved = "resolved"


class SystemStatus(str, Enum):
    """Overall status-page state derived from active incidents."""

    operational = "operational"
    degraded_performance = "degraded_performance"
    minor_outage = "minor_outage"
    partial_outage = "partial_outage"
    major_outage = "major_outage"


# Which status-page state each severity maps to when it is the worst active.
_SEVERITY_TO_STATUS: dict[Severity, SystemStatus] = {
    Severity.sev1: SystemStatus.major_outage,
    Severity.sev2: SystemStatus.partial_outage,
    Severity.sev3: SystemStatus.minor_outage,
    Severity.sev4: SystemStatus.degraded_performance,
}

# Legal status transitions. Anything not listed here is rejected.
_INCIDENT_TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.investigating: frozenset(
        {IncidentStatus.identified, IncidentStatus.monitoring, IncidentStatus.resolved}
    ),
    IncidentStatus.identified: frozenset(
        {IncidentStatus.investigating, IncidentStatus.monitoring, IncidentStatus.resolved}
    ),
    IncidentStatus.monitoring: frozenset(
        {IncidentStatus.investigating, IncidentStatus.identified, IncidentStatus.resolved}
    ),
    IncidentStatus.resolved: frozenset(),
}


class TransitionError(RuntimeError):
    """Raised when an illegal incident status transition is attempted."""


class PostmortemError(RuntimeError):
    """Raised when a postmortem is attached to an unresolved incident."""


class IncidentUpdate(BaseModel):
    """A single chronological update on an incident's timeline."""

    model_config = ConfigDict(extra="forbid")

    at: str = Field(min_length=1)
    message: str = Field(min_length=1)
    status: IncidentStatus | None = None


class ActionItem(BaseModel):
    """A follow-up action recorded in a postmortem."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    owner: str = ""
    done: bool = False


class Postmortem(BaseModel):
    """A structured postmortem attached to a resolved incident."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    impact: str = ""
    root_cause: str = ""
    action_items: list[ActionItem] = Field(default_factory=list)


class Incident(BaseModel):
    """An operational incident with a chronological timeline.

    The ``id`` and all timestamps are caller-supplied for determinism.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    severity: Severity
    status: IncidentStatus = IncidentStatus.investigating
    affected_components: list[str] = Field(default_factory=list)
    started_at: str = Field(min_length=1)
    resolved_at: str | None = None
    timeline: list[IncidentUpdate] = Field(default_factory=list)
    postmortem: Postmortem | None = None

    @property
    def is_active(self) -> bool:
        """True while the incident is not yet resolved."""
        return self.status != IncidentStatus.resolved

    def add_update(
        self, *, now: str, message: str, status: IncidentStatus | None = None
    ) -> IncidentUpdate:
        """Append a timeline update, optionally transitioning status.

        When ``status`` is provided it must be a legal transition. Moving to
        ``resolved`` stamps ``resolved_at`` with ``now``.
        """
        if status is not None:
            allowed = _INCIDENT_TRANSITIONS[self.status]
            if status not in allowed:
                raise TransitionError(
                    f"illegal incident transition {self.status.value} -> {status.value}"
                )
        update = IncidentUpdate(at=now, message=message, status=status)
        self.timeline.append(update)
        if status is not None:
            self.status = status
            if status == IncidentStatus.resolved:
                self.resolved_at = now
        return update

    def time_to_resolve_seconds(self) -> float | None:
        """Seconds between ``started_at`` and ``resolved_at``.

        Returns ``None`` while the incident is unresolved.
        """
        if self.resolved_at is None:
            return None
        start = _parse(self.started_at)
        end = _parse(self.resolved_at)
        return (end - start).total_seconds()

    def attach_postmortem(self, postmortem: Postmortem) -> None:
        """Attach a postmortem. Fails closed unless the incident is resolved."""
        if self.status != IncidentStatus.resolved:
            raise PostmortemError(
                "postmortem can only be attached to a resolved incident"
            )
        self.postmortem = postmortem


def _parse(value: str) -> datetime:
    """Parse an ISO 8601 timestamp, tolerating a trailing ``Z``."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def system_status(incidents: list[Incident]) -> SystemStatus:
    """Derive overall system status from a list of incidents.

    Only active (unresolved) incidents count. The worst active severity wins.
    With no active incidents the result is ``operational``.
    """
    active = [i for i in incidents if i.is_active]
    if not active:
        return SystemStatus.operational
    worst = min(active, key=lambda i: _SEVERITY_RANK[i.severity])
    return _SEVERITY_TO_STATUS[worst.severity]
