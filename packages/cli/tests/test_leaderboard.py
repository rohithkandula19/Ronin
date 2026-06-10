"""Tests for the Dojo leaderboard — tallies, standings, recommendation."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.leaderboard import (
    Leaderboard,
    MIN_CONTESTS,
    load_leaderboard,
    save_leaderboard,
)


def test_record_tallies_wins_and_contests() -> None:
    lb = Leaderboard()
    lb.record("anthropic", ["anthropic", "gemini", "cerebras"])
    lb.record("anthropic", ["anthropic", "gemini"])
    lb.record("gemini", ["anthropic", "gemini"])
    assert lb.data["anthropic"] == [2, 3]   # 2 wins / 3 contests
    assert lb.data["gemini"] == [1, 3]
    assert lb.data["cerebras"] == [0, 1]


def test_win_rate_and_standings() -> None:
    lb = Leaderboard()
    for _ in range(4):
        lb.record("anthropic", ["anthropic", "gemini"])
    lb.record("gemini", ["anthropic", "gemini"])
    assert lb.win_rate("anthropic") == 4 / 5
    standings = lb.standings()
    assert standings[0]["provider"] == "anthropic"   # higher rate ranks first


def test_recommend_needs_enough_contests() -> None:
    lb = Leaderboard()
    lb.record("anthropic", ["anthropic", "gemini"])   # only 1 contest
    assert lb.recommend_strong() is None
    for _ in range(MIN_CONTESTS):
        lb.record("anthropic", ["anthropic", "gemini"])
    assert lb.recommend_strong() == "anthropic"


def test_no_clear_leader_no_recommendation() -> None:
    lb = Leaderboard()
    # alternate winners → top rate not > 0.5
    for w in ("a", "b", "a", "b", "a", "b"):
        lb.record(w, ["a", "b"])
    assert lb.recommend_strong() is None


def test_round_trip(tmp_path: Path) -> None:
    lb = Leaderboard()
    lb.record("anthropic", ["anthropic", "gemini"])
    save_leaderboard(tmp_path, lb)
    assert (tmp_path / ".ronin" / "dojo_leaderboard.json").is_file()
    assert load_leaderboard(tmp_path).data["anthropic"] == [1, 1]
