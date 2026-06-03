"""Edge-case coverage across bushido, sentinel, recall, leaderboard, savings, swarm."""
from __future__ import annotations

from pathlib import Path

import pytest


# ---- bushido ----

def test_bushido_template_created_with_user(monkeypatch, tmp_path: Path) -> None:
    from ronin_cli import bushido
    monkeypatch.setenv("RONIN_HOME", str(tmp_path / ".ronin"))
    bushido.remember_rule("rule a")
    text = bushido.load_bushido()
    assert "## Code" in text and "rule a" in text


def test_bushido_strips_leading_dash(monkeypatch, tmp_path: Path) -> None:
    from ronin_cli import bushido
    monkeypatch.setenv("RONIN_HOME", str(tmp_path / ".ronin"))
    bushido.remember_rule("- already bulleted")
    text = bushido.load_bushido()
    assert "- already bulleted" in text
    assert "- - already bulleted" not in text


# ---- sentinel ----

@pytest.mark.parametrize("text,level", [
    ("ok\nCONFIDENCE: high", "high"),
    ("ok\nconfidence: LOW", "low"),
    ("CONFIDENCE:medium", "medium"),   # \s* allows zero spaces after the colon
    ("no marker at all", None),
])
def test_sentinel_levels(text, level) -> None:
    from ronin_cli.sentinel import parse_confidence
    assert parse_confidence(text) == level


def test_sentinel_badge_colours() -> None:
    from ronin_cli.sentinel import confidence_badge
    assert "green" in confidence_badge("x\nCONFIDENCE: high")
    assert "red" in confidence_badge("x\nCONFIDENCE: low")


# ---- recall ----

def test_recall_score_case_insensitive() -> None:
    from ronin_cli.recall import score_text
    assert score_text("Auth AUTH auth", ["auth"]) == 3


def test_recall_rank_limit() -> None:
    from ronin_cli.recall import rank
    items = [({"id": f"s{i}", "title": "t"}, [f"USER: auth thing {i}"]) for i in range(20)]
    assert len(rank(items, "auth", limit=5)) == 5


# ---- leaderboard ----

def test_leaderboard_win_rate_none_without_data() -> None:
    from ronin_cli.leaderboard import Leaderboard
    assert Leaderboard().win_rate("anthropic") is None


def test_leaderboard_standings_sorted() -> None:
    from ronin_cli.leaderboard import Leaderboard
    lb = Leaderboard()
    for _ in range(3):
        lb.record("a", ["a", "b"])
    lb.record("b", ["a", "b"])
    s = lb.standings()
    assert s[0]["provider"] == "a" and s[0]["rate"] > s[1]["rate"]


# ---- savings ----

def test_savings_summary_keys(tmp_path: Path) -> None:
    from ronin_cli.savings import load_summary
    s = load_summary(tmp_path)
    assert set(s) == {"spent", "baseline", "saved", "turns", "free_turns"}


# ---- swarm ----

def test_swarm_roster_whitespace_tolerant() -> None:
    from ronin_cli.swarm import parse_roster
    ra = parse_roster(" architect = anthropic , reviewer = gemini ")
    assert ra.architect == "anthropic" and ra.reviewer == "gemini"


# ---- nightshift signature ----

def test_nightshift_signature_changes_with_ref() -> None:
    from ronin_cli.nightshift import Task, task_signature
    a = Task("issue", "title", "body", ref="#1")
    b = Task("issue", "title", "body", ref="#2")
    assert task_signature(a) != task_signature(b)
