"""Tests for the mission-control dashboard."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.config import RoninConfig
from ronin_cli.dashboard import gather, render


def test_gather_shape(tmp_path: Path) -> None:
    data = gather(tmp_path, RoninConfig(provider="cerebras", model="gpt-oss-120b"))
    assert data["provider"] == "cerebras" and data["model"] == "gpt-oss-120b"
    assert set(data) >= {"provider", "model", "routing", "git", "savings",
                         "leaderboard", "router", "sessions", "nightshift"}
    assert data["routing"] is None                 # not configured
    assert data["sessions"] == 0
    assert data["nightshift"] == {"total": 0, "patched": 0}


def test_gather_reads_savings(tmp_path: Path) -> None:
    from ronin_cli.savings import append_turn
    append_turn(tmp_path, actual=0.0, baseline=0.05, free=True)
    data = gather(tmp_path, RoninConfig())
    assert data["savings"]["turns"] == 1 and data["savings"]["free_turns"] == 1


def test_gather_reads_leaderboard(tmp_path: Path) -> None:
    from ronin_cli.leaderboard import Leaderboard, save_leaderboard
    lb = Leaderboard()
    for _ in range(3):
        lb.record("anthropic", ["anthropic", "gemini"])
    save_leaderboard(tmp_path, lb)
    data = gather(tmp_path, RoninConfig())
    assert data["leaderboard"] and data["leaderboard"][0]["provider"] == "anthropic"


def test_render_no_crash(tmp_path: Path) -> None:
    from rich.console import Console
    data = gather(tmp_path, RoninConfig(provider="anthropic"))
    render(Console(file=open("/dev/null", "w"), width=100), data)
