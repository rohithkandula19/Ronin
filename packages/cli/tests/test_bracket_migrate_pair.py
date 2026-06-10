"""Tests for tournament bracket, migrate discovery, and pair prompt."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ronin_cli.bracket import make_matchups, pick_winner, run_tournament
from ronin_cli.config import RoninConfig
from ronin_cli.migrate import find_targets, to_tasks
from ronin_cli.pair import pair_prompt


# ---- bracket ----

def test_make_matchups_even() -> None:
    assert make_matchups(["a", "b", "c", "d"]) == [("a", "b"), ("c", "d")]


def test_make_matchups_odd_gives_bye() -> None:
    ms = make_matchups(["a", "b", "c"])
    assert ms == [("a", "b"), ("c", None)]


def test_pick_winner() -> None:
    assert pick_winner("Answer 2 is better.\nWINNER: 2") == 1
    assert pick_winner("WINNER: 1") == 0
    assert pick_winner("no verdict") is None


def test_run_tournament_crowns_a_champion() -> None:
    cfg = RoninConfig(provider="anthropic")
    specs = [{"provider": "anthropic"}, {"provider": "gemini"},
             {"provider": "cerebras"}, {"provider": "groq"}]

    # judge always says WINNER: 1 → the first answer (answer 'a') wins every match
    def fake_ask(base, spec, system, prompt, max_tokens=1200):
        return "WINNER: 1" if "judging" in system.lower() else "an answer"
    with patch("ronin_cli.debate._ask", side_effect=fake_ask):
        res = run_tournament(cfg, "Q?", specs)
    assert res.champion                       # someone won
    assert len(res.rounds) == 2               # 4 → 2 → 1


def test_run_tournament_three_has_a_bye() -> None:
    cfg = RoninConfig(provider="anthropic")
    specs = [{"provider": "a"}, {"provider": "b"}, {"provider": "c"}]
    with patch("ronin_cli.debate._ask", side_effect=lambda *a, **k: "WINNER: 1"):
        res = run_tournament(cfg, "Q?", specs)
    assert res.champion
    # round 1 has a bye matchup (b is None)
    assert any(m.b is None for m in res.rounds[0])


# ---- migrate ----

def test_find_targets_by_contains(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import requests\nrequests.get('x')\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("requests\n", encoding="utf-8")
    targets = find_targets(tmp_path, glob="*.py", contains="requests")
    paths = {t.path for t in targets}
    assert "a.py" in paths and "c.py" in paths and "b.py" not in paths
    # ranked by hit count → a.py (2) before c.py (1)
    assert targets[0].path == "a.py" and targets[0].hits == 2


def test_find_targets_glob(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("a", encoding="utf-8")
    (tmp_path / "y.txt").write_text("a", encoding="utf-8")
    assert {t.path for t in find_targets(tmp_path, glob="*.py")} == {"x.py"}


def test_find_targets_skips_vendor(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "z.py").write_text("requests", encoding="utf-8")
    assert find_targets(tmp_path, glob="*.py", contains="requests") == []


def test_to_tasks() -> None:
    from ronin_cli.migrate import Target
    tasks = to_tasks("use httpx", [Target("a.py", 2), Target("b.py", 1)])
    assert len(tasks) == 2 and tasks[0].source == "migrate"
    assert "a.py" in tasks[0].title and "use httpx" in tasks[0].detail


# ---- pair ----

def test_pair_prompt() -> None:
    p = pair_prompt(["auth.py"], "diff --git a/auth.py\n+token = None")
    assert "auth.py" in p and "token = None" in p and "one note" in p.lower()
