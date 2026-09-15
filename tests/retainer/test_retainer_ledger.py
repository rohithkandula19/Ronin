"""The effect ledger: what a Retainer has already done to the outside world.

Two tests here carry the design rather than merely covering it.
``test_two_simultaneous_deliveries_produce_one_claim`` runs real threads against
one file, because "the primary key decides" is a claim about sqlite and not about
my reading of it. ``test_a_schema_it_does_not_understand_is_refused_not_dropped``
pins the difference from the session index: that file is a cache and may be
rebuilt, this one is the record and may not.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from ronin.retainer.ledger import (
    LEDGER_FILENAME,
    LEDGER_SCHEMA_VERSION,
    Claim,
    EffectLedger,
    EffectStatus,
    LedgerError,
    once,
)
from ronin.retainer.model import Effect, EffectKind


def effect(**kwargs: object) -> Effect:
    base: dict[str, object] = {
        "retainer": "sentry",
        "summons": "sum-1",
        "step": "reply",
        "kind": EffectKind.COMMENT,
        "target": "rohithkandula19/Ronin#258",
        "body": "all fourteen checks are green",
    }
    base.update(kwargs)
    return Effect(**base)  # type: ignore[arg-type]


def ledger(tmp_path: Path, **kwargs: object) -> EffectLedger:
    return EffectLedger.open(tmp_path / "retainer", **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Claim, then complete
# --------------------------------------------------------------------------- #


def test_an_unseen_effect_can_be_claimed(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    assert book.status(effect()) is EffectStatus.UNSEEN
    assert book.claim(effect()) is not None


def test_a_redelivery_of_the_same_effect_is_refused(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    first = book.claim(effect())
    assert first is not None
    book.complete(first, result="https://github.com/…#issuecomment-1")
    assert book.claim(effect()) is None


def test_an_edited_body_is_a_different_effect_and_may_be_claimed(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    claimed = book.claim(effect(body="green"))
    assert claimed is not None
    book.complete(claimed)
    assert book.claim(effect(body="red")) is not None


def test_a_claim_that_is_never_completed_reads_as_unknown(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    assert book.claim(effect()) is not None
    assert book.status(effect()) is EffectStatus.PENDING


def test_completing_marks_it_done(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    claimed = book.claim(effect())
    assert claimed is not None
    book.complete(claimed, result="posted")
    assert book.status(effect()) is EffectStatus.DONE


def test_completing_twice_is_an_error_rather_than_a_shrug(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    claimed = book.claim(effect())
    assert claimed is not None
    book.complete(claimed)
    with pytest.raises(LedgerError, match="no open claim"):
        book.complete(claimed)


def test_completing_a_claim_nobody_holds_is_an_error(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    with pytest.raises(LedgerError, match="no open claim"):
        book.complete(Claim(effect=effect(), claimed_at=0.0))


# --------------------------------------------------------------------------- #
# The race the API exists to lose safely
# --------------------------------------------------------------------------- #


def test_two_simultaneous_deliveries_produce_one_claim(tmp_path: Path) -> None:
    """Real threads on one file. The primary key decides, not the read order."""
    book = ledger(tmp_path)
    start = threading.Barrier(8)
    won: list[bool] = []
    lock = threading.Lock()

    def deliver() -> None:
        start.wait()
        claimed = book.claim(effect())
        with lock:
            won.append(claimed is not None)

    threads = [threading.Thread(target=deliver) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert won.count(True) == 1, f"expected exactly one winner, got {won.count(True)}"


def test_a_second_process_sees_the_first_ones_claim(tmp_path: Path) -> None:
    first = ledger(tmp_path)
    assert first.claim(effect()) is not None
    second = EffectLedger.open(tmp_path / "retainer")
    assert second.claim(effect()) is None
    assert second.status(effect()) is EffectStatus.PENDING


# --------------------------------------------------------------------------- #
# Abandoning, which is only ever for a provable non-event
# --------------------------------------------------------------------------- #


def test_abandoning_lets_the_effect_be_claimed_again(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    claimed = book.claim(effect())
    assert claimed is not None
    book.abandon(claimed)
    assert book.status(effect()) is EffectStatus.UNSEEN
    assert book.claim(effect()) is not None


def test_abandoning_cannot_undo_something_that_happened(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    claimed = book.claim(effect())
    assert claimed is not None
    book.complete(claimed)
    book.abandon(claimed)
    assert book.status(effect()) is EffectStatus.DONE


# --------------------------------------------------------------------------- #
# Surfacing the unknowns
# --------------------------------------------------------------------------- #


def test_pending_lists_open_claims_oldest_first(tmp_path: Path) -> None:
    ticks = iter([1.0, 2.0, 3.0, 4.0])
    book = ledger(tmp_path, clock=lambda: next(ticks))
    assert book.claim(effect(step="summary")) is not None
    assert book.claim(effect(step="reply")) is not None
    assert [row.step for row in book.pending()] == ["summary", "reply"]


def test_pending_hides_what_completed(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    claimed = book.claim(effect())
    assert claimed is not None
    book.complete(claimed)
    assert book.pending() == ()


def test_pending_can_be_narrowed_to_one_retainer(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    assert book.claim(effect()) is not None
    assert book.claim(effect(retainer="scout")) is not None
    assert [row.retainer for row in book.pending("scout")] == ["scout"]
    assert len(book.pending()) == 2


def test_a_pending_row_says_it_may_or_may_not_have_happened(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    assert book.claim(effect()) is not None
    (row,) = book.pending()
    text = row.describe()
    assert "may or may not have happened" in text
    assert "rohithkandula19/Ronin#258" in text
    assert row.kind is EffectKind.COMMENT


def test_done_for_lists_completed_steps_so_a_resume_can_skip_them(tmp_path: Path) -> None:
    ticks = iter([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    book = ledger(tmp_path, clock=lambda: next(ticks))
    for step in ("clone", "push", "reply"):
        claimed = book.claim(effect(step=step))
        assert claimed is not None
        book.complete(claimed)
    assert book.done_for("sum-1") == ("clone", "push", "reply")


def test_done_for_ignores_a_claim_that_never_closed(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    claimed = book.claim(effect(step="clone"))
    assert claimed is not None
    book.complete(claimed)
    assert book.claim(effect(step="push")) is not None
    assert book.done_for("sum-1") == ("clone",)


# --------------------------------------------------------------------------- #
# `once`, and what it deliberately does not do
# --------------------------------------------------------------------------- #


def test_once_yields_a_claim_the_first_time_and_nothing_after(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    posted: list[str] = []
    for claim in once(book, effect()):
        posted.append(effect().body)
        book.complete(claim, result="url")
    for _claim in once(book, effect()):
        posted.append("second")
    assert posted == ["all fourteen checks are green"]


def test_once_does_not_complete_a_claim_whose_act_raised(tmp_path: Path) -> None:
    """An act that blew up leaves the claim open. That is the point."""
    book = ledger(tmp_path)
    with pytest.raises(RuntimeError, match="network"):
        for _claim in once(book, effect()):
            raise RuntimeError("network went away mid-post")
    assert book.status(effect()) is EffectStatus.PENDING


# --------------------------------------------------------------------------- #
# Not a cache — the three inversions of persistence.index
# --------------------------------------------------------------------------- #


def test_a_schema_it_does_not_understand_is_refused_not_dropped(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    claimed = book.claim(effect())
    assert claimed is not None
    book.complete(claimed, result="posted")

    with sqlite3.connect(book.path) as conn:
        conn.execute(f"PRAGMA user_version={LEDGER_SCHEMA_VERSION + 1}")

    with pytest.raises(LedgerError, match="not safe to discard"):
        EffectLedger.open(tmp_path / "retainer")

    # And the row it was protecting is still there.
    with sqlite3.connect(book.path) as conn:
        assert conn.execute("SELECT count(*) FROM effects").fetchone()[0] == 1


def test_a_file_that_is_not_a_database_raises_rather_than_degrades(tmp_path: Path) -> None:
    directory = tmp_path / "retainer"
    directory.mkdir()
    (directory / LEDGER_FILENAME).write_bytes(b"this is not a database")
    with pytest.raises(LedgerError, match="cannot prepare"):
        EffectLedger.open(directory)


def test_reads_raise_when_the_file_is_destroyed_under_us(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.path.write_bytes(b"clobbered")
    with pytest.raises(LedgerError, match="cannot read"):
        book.status(effect())
    with pytest.raises(LedgerError, match="cannot read"):
        book.pending()
    with pytest.raises(LedgerError, match="cannot read"):
        book.done_for("sum-1")


def test_writes_raise_when_the_file_is_destroyed_under_us(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    claimed = book.claim(effect())
    assert claimed is not None
    book.path.write_bytes(b"clobbered")
    with pytest.raises(LedgerError, match="cannot claim"):
        book.claim(effect(step="other"))
    with pytest.raises(LedgerError, match="cannot complete"):
        book.complete(claimed)
    with pytest.raises(LedgerError, match="cannot abandon"):
        book.abandon(claimed)


def test_opening_creates_the_directory_it_was_given(tmp_path: Path) -> None:
    book = EffectLedger.open(tmp_path / "deep" / "nested")
    assert book.path == tmp_path / "deep" / "nested" / LEDGER_FILENAME
    assert book.path.exists()


def test_reopening_an_existing_ledger_keeps_its_rows(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    claimed = book.claim(effect())
    assert claimed is not None
    book.complete(claimed)
    assert EffectLedger.open(tmp_path / "retainer").status(effect()) is EffectStatus.DONE


def test_a_path_that_cannot_be_opened_at_all_raises(tmp_path: Path) -> None:
    """``sqlite3.connect`` is lazy, so this is the one shape that fails in it."""
    directory = tmp_path / "retainer"
    directory.mkdir()
    (directory / LEDGER_FILENAME).mkdir()
    with pytest.raises(LedgerError, match="cannot open the effect ledger"):
        EffectLedger.open(directory)
