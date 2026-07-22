"""Tests for the support tickets module."""

from __future__ import annotations

import pytest

from ronin_support import (
    Priority,
    Ticket,
    TicketNotFoundError,
    TicketStatus,
    TicketStore,
    TicketTransitionError,
)

T0 = "2026-07-22T09:00:00+00:00"
T1 = "2026-07-22T10:00:00+00:00"
T2 = "2026-07-22T11:00:00+00:00"


def _store_with_ticket() -> tuple[TicketStore, Ticket]:
    store = TicketStore()
    ticket = store.open_ticket(
        id="tkt_1",
        org_id="org_a",
        requester="user@example.com",
        subject="Cannot log in",
        now=T0,
        body="I get an error on login.",
        priority=Priority.high,
    )
    return store, ticket


def test_open_ticket_defaults_and_created_event() -> None:
    _, ticket = _store_with_ticket()
    assert ticket.status == TicketStatus.open
    assert ticket.priority == Priority.high
    assert ticket.created_at == T0
    assert ticket.updated_at == T0
    assert len(ticket.timeline) == 1
    assert ticket.timeline[0].kind == "created"


def test_duplicate_id_rejected() -> None:
    store, _ = _store_with_ticket()
    with pytest.raises(ValueError):
        store.open_ticket(
            id="tkt_1",
            org_id="org_a",
            requester="user@example.com",
            subject="dup",
            now=T0,
        )


def test_add_comment_appends_and_bumps_updated() -> None:
    _, ticket = _store_with_ticket()
    event = ticket.add_comment(now=T1, actor="agent", message="Looking into it")
    assert event.kind == "comment"
    assert ticket.updated_at == T1
    assert ticket.timeline[-1].message == "Looking into it"


def test_empty_comment_rejected() -> None:
    _, ticket = _store_with_ticket()
    with pytest.raises(ValueError):
        ticket.add_comment(now=T1, actor="agent", message="")


def test_legal_transition_open_to_pending_to_resolved_to_closed() -> None:
    _, ticket = _store_with_ticket()
    ticket.transition(now=T1, actor="agent", to_status=TicketStatus.pending)
    assert ticket.status == TicketStatus.pending
    ticket.transition(now=T1, actor="agent", to_status=TicketStatus.resolved)
    assert ticket.status == TicketStatus.resolved
    ticket.transition(now=T2, actor="agent", to_status=TicketStatus.closed)
    assert ticket.status == TicketStatus.closed
    kinds = [e.kind for e in ticket.timeline]
    assert kinds.count("status_change") == 3


def test_reopen_from_closed_is_legal() -> None:
    _, ticket = _store_with_ticket()
    ticket.transition(now=T1, actor="agent", to_status=TicketStatus.closed)
    ticket.transition(now=T2, actor="agent", to_status=TicketStatus.open)
    assert ticket.status == TicketStatus.open


def test_illegal_transition_raises() -> None:
    _, ticket = _store_with_ticket()
    ticket.transition(now=T1, actor="agent", to_status=TicketStatus.closed)
    # closed -> pending is not allowed
    with pytest.raises(TicketTransitionError):
        ticket.transition(now=T2, actor="agent", to_status=TicketStatus.pending)


def test_noop_transition_is_illegal() -> None:
    _, ticket = _store_with_ticket()
    with pytest.raises(TicketTransitionError):
        ticket.transition(now=T1, actor="agent", to_status=TicketStatus.open)


def test_transition_records_from_and_to_status() -> None:
    _, ticket = _store_with_ticket()
    event = ticket.transition(now=T1, actor="agent", to_status=TicketStatus.pending)
    assert event.from_status == TicketStatus.open
    assert event.to_status == TicketStatus.pending


def test_assign_ticket() -> None:
    _, ticket = _store_with_ticket()
    ticket.assign(now=T1, actor="lead", assignee="agent-42")
    assert ticket.assignee == "agent-42"
    assert ticket.timeline[-1].kind == "assignment"
    assert ticket.updated_at == T1


def test_assign_empty_rejected() -> None:
    _, ticket = _store_with_ticket()
    with pytest.raises(ValueError):
        ticket.assign(now=T1, actor="lead", assignee="")


def test_extra_field_forbidden() -> None:
    with pytest.raises(Exception):
        Ticket(
            id="x",
            org_id="o",
            requester="r",
            subject="s",
            created_at=T0,
            updated_at=T0,
            bogus=True,
        )


def test_get_within_org() -> None:
    store, _ = _store_with_ticket()
    got = store.get(org_id="org_a", ticket_id="tkt_1")
    assert got.id == "tkt_1"


def test_cross_org_read_denied() -> None:
    store, _ = _store_with_ticket()
    with pytest.raises(TicketNotFoundError):
        store.get(org_id="org_b", ticket_id="tkt_1")


def test_missing_ticket_not_found() -> None:
    store, _ = _store_with_ticket()
    with pytest.raises(TicketNotFoundError):
        store.get(org_id="org_a", ticket_id="does_not_exist")


def test_list_is_org_scoped() -> None:
    store, _ = _store_with_ticket()
    store.open_ticket(
        id="tkt_2",
        org_id="org_b",
        requester="other@example.com",
        subject="Other org issue",
        now=T1,
    )
    org_a = store.list_for_org(org_id="org_a")
    org_b = store.list_for_org(org_id="org_b")
    assert [t.id for t in org_a] == ["tkt_1"]
    assert [t.id for t in org_b] == ["tkt_2"]


def test_list_filter_by_status() -> None:
    store, ticket = _store_with_ticket()
    store.open_ticket(
        id="tkt_3",
        org_id="org_a",
        requester="user@example.com",
        subject="Second",
        now=T1,
    )
    ticket.transition(now=T2, actor="agent", to_status=TicketStatus.resolved)
    store.save(ticket)
    resolved = store.list_for_org(org_id="org_a", status=TicketStatus.resolved)
    assert [t.id for t in resolved] == ["tkt_1"]
    open_ones = store.list_for_org(org_id="org_a", status=TicketStatus.open)
    assert [t.id for t in open_ones] == ["tkt_3"]


def test_list_sorted_deterministically() -> None:
    store = TicketStore()
    store.open_ticket(
        id="b", org_id="org_a", requester="r", subject="s", now=T1
    )
    store.open_ticket(
        id="a", org_id="org_a", requester="r", subject="s", now=T0
    )
    ids = [t.id for t in store.list_for_org(org_id="org_a")]
    assert ids == ["a", "b"]
