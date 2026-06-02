"""Tests for the Dojo's pure logic — winner parsing + contender selection."""
from __future__ import annotations

from ro_claude_kit_cli.dojo import DojoResult, Fighter, pick_winner


def test_pick_winner_valid() -> None:
    assert pick_winner("Candidate 2 is best.\nWINNER: 2", 3) == 1   # 0-based


def test_pick_winner_out_of_range() -> None:
    assert pick_winner("WINNER: 9", 3) is None
    assert pick_winner("WINNER: 0", 3) is None


def test_pick_winner_missing() -> None:
    assert pick_winner("they're all fine honestly", 3) is None


def test_fighter_changed_flag() -> None:
    assert Fighter("a", diff="+x\n").changed
    assert not Fighter("b", diff="   ").changed
    assert not Fighter("c", ok=False, error="boom").changed


def test_result_winner_and_contenders() -> None:
    r = DojoResult(task="t", fighters=[
        Fighter("a", diff="+1\n"),
        Fighter("b", ok=False, error="x"),
        Fighter("c", diff="+2\n"),
    ], winner_index=2)
    assert r.winner.label == "c"
    assert [f.label for f in r.contenders] == ["a", "c"]


def test_result_no_winner() -> None:
    r = DojoResult(task="t", fighters=[Fighter("a", diff="")], winner_index=None)
    assert r.winner is None
    assert r.contenders == []
