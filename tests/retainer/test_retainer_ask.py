"""Asking a human for authority when the run that needs it has already ended.

Three tests carry the design. ``test_the_asker_returns_rather_than_blocking``
pins the contract that makes a multi-day pause possible at all;
``test_the_refusal_is_detail_not_feedback`` pins the distinction that stops the
engine quoting words nobody said; and
``test_yes_for_this_session_is_refused`` pins that an escalation outlives the
session it was raised in.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from ronin.core.types import ApprovalRequest, DangerLevel
from ronin.retainer.ask import (
    ASKED_AND_WAITING,
    ESCALATIONS_FILENAME,
    ESCALATIONS_SCHEMA_VERSION,
    EscalationError,
    EscalationStore,
    NotAllowedToAnswer,
    ThreadAsker,
    nobody,
    owner_only,
)
from ronin.retainer.model import Escalation, EscalationState
from ronin.safety.policy import Answer, Asker, Outcome

OWNER = "rohithkandula19"
COLLABORATOR = "someone-else"


def store(tmp_path: Path, **kwargs: object) -> EscalationStore:
    return EscalationStore.open(tmp_path / "retainer", **kwargs)  # type: ignore[arg-type]


def escalation(**kwargs: object) -> Escalation:
    base: dict[str, object] = {
        "id": "esc-1",
        "retainer": "sentry",
        "thread": "258",
        "tool": "bash",
        "request": "git push --force origin main",
        "session": "20260906-104500-abc123",
        "checkpoint": "ckpt-7",
    }
    base.update(kwargs)
    return Escalation(**base)  # type: ignore[arg-type]


def request(**kwargs: object) -> ApprovalRequest:
    base: dict[str, object] = {
        "tool_use_id": "tu-1",
        "name": "bash",
        "danger_level": DangerLevel.DESTRUCTIVE,
        "rendered": "git push --force origin main",
    }
    base.update(kwargs)
    return ApprovalRequest(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The asker: record and stop
# --------------------------------------------------------------------------- #


def test_the_asker_satisfies_the_protocol() -> None:
    assert isinstance(ThreadAsker.__new__(ThreadAsker), Asker)


def test_the_asker_returns_rather_than_blocking(tmp_path: Path) -> None:
    """The contract that makes a multi-day pause possible: it must not wait."""
    asker = ThreadAsker(store=store(tmp_path), retainer="sentry", thread="258")

    async def drive() -> Answer:
        return await asyncio.wait_for(asker.ask(request()), timeout=2)

    answer = asyncio.run(drive())
    assert not answer.approves


def test_the_refusal_is_detail_not_feedback(tmp_path: Path) -> None:
    """Nobody has spoken yet, so there is nothing to quote back to the model."""
    asker = ThreadAsker(store=store(tmp_path), retainer="sentry", thread="258")
    answer = asyncio.run(asker.ask(request()))
    assert answer.detail == ASKED_AND_WAITING
    assert answer.feedback == ""
    assert answer.outcome is Outcome.NO


def test_asking_records_what_a_human_needs_to_decide(tmp_path: Path) -> None:
    book = store(tmp_path)
    asker = ThreadAsker(
        store=book,
        retainer="sentry",
        thread="258",
        session="20260906-104500-abc123",
        checkpoint="ckpt-7",
        mint=lambda: "esc-1",
    )
    asyncio.run(asker.ask(request()))
    waiting = book.lookup("esc-1")
    assert waiting is not None
    assert waiting.tool == "bash"
    assert waiting.request == "git push --force origin main"
    assert (waiting.session, waiting.checkpoint) == ("20260906-104500-abc123", "ckpt-7")
    assert waiting.open


def test_the_asker_reports_the_ids_it_raised_so_they_can_be_posted(
    tmp_path: Path,
) -> None:
    ids = iter(["esc-1", "esc-2"])
    asker = ThreadAsker(
        store=store(tmp_path), retainer="sentry", thread="258", mint=lambda: next(ids)
    )
    asyncio.run(asker.ask(request()))
    asyncio.run(asker.ask(request(rendered="rm -rf build")))
    assert asker.raised == ["esc-1", "esc-2"]


def test_the_default_id_is_unguessable_because_it_appears_in_public(
    tmp_path: Path,
) -> None:
    asker = ThreadAsker(store=store(tmp_path), retainer="sentry", thread="258")
    asyncio.run(asker.ask(request()))
    asyncio.run(asker.ask(request(rendered="rm -rf build")))
    first, second = asker.raised
    assert first != second
    assert len(first) > len("esc-") + 8


# --------------------------------------------------------------------------- #
# Who may answer
# --------------------------------------------------------------------------- #


def test_an_unconfigured_store_lets_nobody_answer(tmp_path: Path) -> None:
    """Forgetting the predicate gets the narrow rule, not an open door."""
    book = store(tmp_path)
    book.raise_(escalation())
    with pytest.raises(NotAllowedToAnswer):
        book.answer("esc-1", Outcome.YES_ONCE, by=OWNER)


def test_the_owner_may_answer(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.raise_(escalation())
    settled = book.answer("esc-1", Outcome.YES_ONCE, by=OWNER, may_answer=owner_only(OWNER))
    assert settled.answer is Outcome.YES_ONCE
    assert settled.answered_by == OWNER
    assert settled.state is EscalationState.ANSWERED


def test_a_collaborator_may_answer_when_the_predicate_says_so(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.raise_(escalation())
    writers = {OWNER, COLLABORATOR}
    settled = book.answer(
        "esc-1", Outcome.NO, by=COLLABORATOR, may_answer=lambda who: who in writers
    )
    assert settled.answered_by == COLLABORATOR


def test_a_stranger_is_refused_with_its_own_error_type(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.raise_(escalation())
    with pytest.raises(NotAllowedToAnswer, match="not allowed to answer"):
        book.answer("esc-1", Outcome.YES_ONCE, by="drive-by", may_answer=owner_only(OWNER))
    assert book.lookup("esc-1") is not None
    found = book.lookup("esc-1")
    assert found is not None and found.open


def test_an_answer_with_no_verified_identity_is_not_an_answer(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.raise_(escalation())
    with pytest.raises(NotAllowedToAnswer, match="no verified identity"):
        book.answer("esc-1", Outcome.YES_ONCE, by="", may_answer=lambda _who: True)


def test_owner_only_refuses_an_empty_identity_even_against_an_empty_owner() -> None:
    assert not owner_only("")("")
    assert not owner_only(OWNER)("")
    assert owner_only(OWNER)(OWNER)


def test_nobody_refuses_everyone() -> None:
    assert not nobody(OWNER)
    assert not nobody("")


# --------------------------------------------------------------------------- #
# An escalation outlives its session
# --------------------------------------------------------------------------- #


def test_yes_for_this_session_is_refused(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.raise_(escalation())
    with pytest.raises(EscalationError, match="nothing to apply to"):
        book.answer("esc-1", Outcome.YES_SESSION, by=OWNER, may_answer=owner_only(OWNER))


def test_an_impossible_answer_is_refused_before_it_is_stored(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.raise_(escalation())
    with pytest.raises(EscalationError):
        book.answer("esc-1", Outcome.YES_SESSION, by=OWNER, may_answer=owner_only(OWNER))
    found = book.lookup("esc-1")
    assert found is not None and found.open


def test_once_and_persist_both_survive_a_round_trip(tmp_path: Path) -> None:
    book = store(tmp_path)
    for index, outcome in enumerate((Outcome.YES_ONCE, Outcome.YES_PERSIST, Outcome.NO)):
        book.raise_(escalation(id=f"esc-{index}"))
        book.answer(f"esc-{index}", outcome, by=OWNER, may_answer=owner_only(OWNER))
        found = book.lookup(f"esc-{index}")
        assert found is not None and found.answer is outcome


# --------------------------------------------------------------------------- #
# The store
# --------------------------------------------------------------------------- #


def test_raising_the_same_escalation_twice_is_refused(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.raise_(escalation())
    with pytest.raises(EscalationError, match="already exists"):
        book.raise_(escalation())


def test_only_an_open_escalation_can_be_raised(tmp_path: Path) -> None:
    book = store(tmp_path)
    settled = escalation(state=EscalationState.ANSWERED, answer=Outcome.YES_ONCE)
    with pytest.raises(EscalationError, match="not open"):
        book.raise_(settled)


def test_answering_twice_is_refused(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.raise_(escalation())
    book.answer("esc-1", Outcome.YES_ONCE, by=OWNER, may_answer=owner_only(OWNER))
    with pytest.raises(EscalationError, match="already answered"):
        book.answer("esc-1", Outcome.NO, by=OWNER, may_answer=owner_only(OWNER))


def test_two_people_answering_at_once_leave_one_winner(tmp_path: Path) -> None:
    """The record's guard cannot see a concurrent answer; the UPDATE decides."""
    import threading

    book = store(tmp_path)
    book.raise_(escalation())
    start = threading.Barrier(6)
    won: list[bool] = []
    lock = threading.Lock()

    def answer() -> None:
        start.wait()
        try:
            book.answer("esc-1", Outcome.YES_ONCE, by=OWNER, may_answer=owner_only(OWNER))
        except EscalationError:
            outcome = False
        else:
            outcome = True
        with lock:
            won.append(outcome)

    workers = [threading.Thread(target=answer) for _ in range(6)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert won.count(True) == 1, f"expected exactly one winner, got {won.count(True)}"


def test_answering_something_that_does_not_exist_says_so(tmp_path: Path) -> None:
    book = store(tmp_path)
    with pytest.raises(EscalationError, match="no escalation"):
        book.answer("esc-nope", Outcome.NO, by=OWNER, may_answer=owner_only(OWNER))


def test_an_unanswered_escalation_never_becomes_a_yes_by_ageing(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.raise_(escalation())
    expired = book.expire("esc-1")
    assert expired.state is EscalationState.EXPIRED
    assert expired.answer is None
    stored = book.lookup("esc-1")
    assert stored is not None and stored.answer is None


def test_expiring_twice_or_expiring_nothing_is_refused(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.raise_(escalation())
    book.expire("esc-1")
    with pytest.raises(EscalationError, match="already expired"):
        book.expire("esc-1")
    with pytest.raises(EscalationError, match="no escalation"):
        book.expire("esc-nope")


def test_an_expired_escalation_cannot_then_be_answered(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.raise_(escalation())
    book.expire("esc-1")
    with pytest.raises(EscalationError, match="already expired"):
        book.answer("esc-1", Outcome.YES_ONCE, by=OWNER, may_answer=owner_only(OWNER))


def test_waiting_is_the_queue_a_human_works_through(tmp_path: Path) -> None:
    ticks = iter([10.0, 20.0, 30.0, 40.0, 50.0])
    book = store(tmp_path, clock=lambda: next(ticks))
    book.raise_(escalation(id="esc-1"))
    book.raise_(escalation(id="esc-2", retainer="scout"))
    assert [e.id for e in book.waiting()] == ["esc-1", "esc-2"]
    assert [e.id for e in book.waiting("scout")] == ["esc-2"]


def test_waiting_drops_what_was_settled(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.raise_(escalation(id="esc-1"))
    book.raise_(escalation(id="esc-2"))
    book.answer("esc-1", Outcome.NO, by=OWNER, may_answer=owner_only(OWNER))
    book.expire("esc-2")
    assert book.waiting() == ()


def test_lookup_of_nothing_is_none(tmp_path: Path) -> None:
    assert store(tmp_path).lookup("esc-nope") is None


def test_escalations_survive_reopening(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.raise_(escalation())
    book.answer("esc-1", Outcome.YES_PERSIST, by=OWNER, may_answer=owner_only(OWNER))
    again = EscalationStore.open(tmp_path / "retainer")
    found = again.lookup("esc-1")
    assert found is not None
    assert (found.answer, found.answered_by) == (Outcome.YES_PERSIST, OWNER)
    assert found.checkpoint == "ckpt-7"


# --------------------------------------------------------------------------- #
# Durability
# --------------------------------------------------------------------------- #


def test_a_schema_it_does_not_understand_is_refused(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.raise_(escalation())
    with sqlite3.connect(book.path) as conn:
        conn.execute(f"PRAGMA user_version={ESCALATIONS_SCHEMA_VERSION + 1}")
    with pytest.raises(EscalationError, match="strands them"):
        EscalationStore.open(tmp_path / "retainer")


def test_a_file_that_is_not_a_database_raises(tmp_path: Path) -> None:
    directory = tmp_path / "retainer"
    directory.mkdir()
    (directory / ESCALATIONS_FILENAME).write_bytes(b"not a database")
    with pytest.raises(EscalationError, match="cannot prepare"):
        EscalationStore.open(directory)


def test_a_path_that_cannot_be_opened_at_all_raises(tmp_path: Path) -> None:
    directory = tmp_path / "retainer"
    directory.mkdir()
    (directory / ESCALATIONS_FILENAME).mkdir()
    with pytest.raises(EscalationError, match="cannot open the escalation store"):
        EscalationStore.open(directory)


def test_every_operation_raises_when_the_file_is_destroyed_under_us(
    tmp_path: Path,
) -> None:
    book = store(tmp_path)
    book.raise_(escalation())
    book.path.write_bytes(b"clobbered")
    with pytest.raises(EscalationError, match="cannot read"):
        book.lookup("esc-1")
    with pytest.raises(EscalationError, match="cannot read"):
        book.waiting()
    with pytest.raises(EscalationError, match="cannot record"):
        book.raise_(escalation(id="esc-2"))


def test_settling_raises_when_the_file_is_destroyed_after_the_read(
    tmp_path: Path,
) -> None:
    """answer() and expire() read first, so the write is a separate failure."""
    book = store(tmp_path)
    book.raise_(escalation())
    healthy = book.lookup("esc-1")
    assert healthy is not None

    class Clobbering(EscalationStore):
        def lookup(self, escalation_id: str) -> Escalation | None:
            self.path.write_bytes(b"clobbered")
            return healthy

    broken = Clobbering(path=book.path)
    with pytest.raises(EscalationError, match="cannot answer"):
        broken.answer("esc-1", Outcome.NO, by=OWNER, may_answer=owner_only(OWNER))
    with pytest.raises(EscalationError, match="cannot expire"):
        broken.expire("esc-1")
