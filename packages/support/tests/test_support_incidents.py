"""Tests for the incidents module (state machine, postmortems, status page)."""

from __future__ import annotations

import pytest

from ronin_support import (
    ActionItem,
    Incident,
    IncidentStatus,
    IncidentTransitionError,
    Postmortem,
    PostmortemError,
    Severity,
    SystemStatus,
    system_status,
)

T0 = "2026-07-22T09:00:00+00:00"
T1 = "2026-07-22T09:30:00+00:00"
T2 = "2026-07-22T10:00:00+00:00"


def _incident(severity: Severity = Severity.sev2) -> Incident:
    return Incident(
        id="inc_1",
        title="API latency spike",
        severity=severity,
        started_at=T0,
        affected_components=["api", "gateway"],
    )


def test_new_incident_is_investigating_and_active() -> None:
    inc = _incident()
    assert inc.status == IncidentStatus.investigating
    assert inc.is_active is True
    assert inc.resolved_at is None


def test_add_update_without_status() -> None:
    inc = _incident()
    update = inc.add_update(now=T1, message="Still looking")
    assert update.status is None
    assert inc.status == IncidentStatus.investigating
    assert len(inc.timeline) == 1


def test_legal_transition_chain_to_resolved() -> None:
    inc = _incident()
    inc.add_update(now=T1, message="found it", status=IncidentStatus.identified)
    inc.add_update(now=T1, message="watching", status=IncidentStatus.monitoring)
    inc.add_update(now=T2, message="all good", status=IncidentStatus.resolved)
    assert inc.status == IncidentStatus.resolved
    assert inc.resolved_at == T2
    assert inc.is_active is False


def test_illegal_transition_from_resolved_raises() -> None:
    inc = _incident()
    inc.add_update(now=T2, message="done", status=IncidentStatus.resolved)
    with pytest.raises(IncidentTransitionError):
        inc.add_update(now=T2, message="reopen?", status=IncidentStatus.investigating)


def test_update_added_before_status_validation_failure_is_not_recorded() -> None:
    inc = _incident()
    inc.add_update(now=T2, message="done", status=IncidentStatus.resolved)
    timeline_len = len(inc.timeline)
    with pytest.raises(IncidentTransitionError):
        inc.add_update(now=T2, message="bad", status=IncidentStatus.identified)
    assert len(inc.timeline) == timeline_len


def test_time_to_resolve_none_while_active() -> None:
    inc = _incident()
    assert inc.time_to_resolve_seconds() is None


def test_time_to_resolve_computed() -> None:
    inc = _incident()
    inc.add_update(now=T2, message="resolved", status=IncidentStatus.resolved)
    # 09:00 -> 10:00 == 3600s
    assert inc.time_to_resolve_seconds() == 3600.0


def test_time_to_resolve_handles_z_suffix() -> None:
    inc = Incident(
        id="inc_z",
        title="z time",
        severity=Severity.sev3,
        started_at="2026-07-22T09:00:00Z",
    )
    inc.add_update(
        now="2026-07-22T09:00:30Z", message="resolved", status=IncidentStatus.resolved
    )
    assert inc.time_to_resolve_seconds() == 30.0


def test_postmortem_blocked_when_not_resolved() -> None:
    inc = _incident()
    pm = Postmortem(summary="s", impact="i", root_cause="rc")
    with pytest.raises(PostmortemError):
        inc.attach_postmortem(pm)
    assert inc.postmortem is None


def test_postmortem_attaches_after_resolution() -> None:
    inc = _incident()
    inc.add_update(now=T2, message="resolved", status=IncidentStatus.resolved)
    pm = Postmortem(
        summary="brief outage",
        impact="5% of requests",
        root_cause="bad deploy",
        action_items=[ActionItem(description="add canary", owner="sre")],
    )
    inc.attach_postmortem(pm)
    assert inc.postmortem is not None
    assert inc.postmortem.action_items[0].description == "add canary"


def test_postmortem_requires_summary() -> None:
    with pytest.raises(Exception):
        Postmortem(summary="")


def test_incident_extra_field_forbidden() -> None:
    with pytest.raises(Exception):
        Incident(
            id="x",
            title="t",
            severity=Severity.sev1,
            started_at=T0,
            bogus=1,
        )


def test_system_status_operational_with_no_incidents() -> None:
    assert system_status([]) == SystemStatus.operational


def test_system_status_ignores_resolved() -> None:
    inc = _incident(Severity.sev1)
    inc.add_update(now=T2, message="resolved", status=IncidentStatus.resolved)
    assert system_status([inc]) == SystemStatus.operational


def test_system_status_worst_active_severity_wins() -> None:
    minor = Incident(
        id="a", title="minor", severity=Severity.sev4, started_at=T0
    )
    major = Incident(
        id="b", title="major", severity=Severity.sev1, started_at=T0
    )
    assert system_status([minor, major]) == SystemStatus.major_outage


def test_system_status_severity_mapping() -> None:
    assert system_status(
        [Incident(id="c", title="x", severity=Severity.sev2, started_at=T0)]
    ) == SystemStatus.partial_outage
    assert system_status(
        [Incident(id="d", title="x", severity=Severity.sev3, started_at=T0)]
    ) == SystemStatus.minor_outage
    assert system_status(
        [Incident(id="e", title="x", severity=Severity.sev4, started_at=T0)]
    ) == SystemStatus.degraded_performance
