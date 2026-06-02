from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli.briefing import compute_briefing_data
from ronin_cli.briefing_history import (
    BriefingDelta,
    BriefingSnapshot,
    format_delta_line,
    load_snapshots,
    most_recent_prior,
    save_snapshot,
)
from ronin_cli.config import RoninConfig
from ronin_cli.main import app
from ronin_cli.tools import build_tools


runner = CliRunner()


def test_snapshot_from_briefing_captures_counts() -> None:
    tools = build_tools(RoninConfig(demo_mode=True))
    data = compute_briefing_data(tools)
    snap = BriefingSnapshot.from_briefing(data, date="2026-05-11")

    assert snap.date == "2026-05-11"
    assert snap.mrr_cents == data.mrr_cents
    assert snap.active_subs == len(data.active_subs)
    assert snap.failed_charges_7d == len(data.failed_charges_7d)


def test_save_and_load_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    snap = BriefingSnapshot(date="2026-05-04", mrr_cents=10_000, active_subs=5)
    save_snapshot(snap)

    loaded = load_snapshots()
    assert len(loaded) == 1
    assert loaded[0].date == "2026-05-04"
    assert loaded[0].mrr_cents == 10_000


def test_load_returns_sorted_oldest_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    save_snapshot(BriefingSnapshot(date="2026-05-11", mrr_cents=3))
    save_snapshot(BriefingSnapshot(date="2026-04-27", mrr_cents=1))
    save_snapshot(BriefingSnapshot(date="2026-05-04", mrr_cents=2))

    dates = [s.date for s in load_snapshots()]
    assert dates == ["2026-04-27", "2026-05-04", "2026-05-11"]


def test_most_recent_prior_excludes_same_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    save_snapshot(BriefingSnapshot(date="2026-04-27", mrr_cents=1))
    save_snapshot(BriefingSnapshot(date="2026-05-04", mrr_cents=2))
    save_snapshot(BriefingSnapshot(date="2026-05-11", mrr_cents=3))

    prior = most_recent_prior("2026-05-11")
    assert prior is not None
    assert prior.date == "2026-05-04"

    # If today's snapshot is the earliest, no prior exists
    assert most_recent_prior("2026-04-01") is None


def test_compute_delta_subtracts_fields() -> None:
    current = BriefingSnapshot(date="2026-05-11", mrr_cents=12_000, new_subs_7d=3, churned_subs_7d=1, failed_charges_7d=2)
    prior = BriefingSnapshot(date="2026-05-04", mrr_cents=10_000, new_subs_7d=2, churned_subs_7d=0, failed_charges_7d=3)

    delta = BriefingDelta.compute(current, prior)
    assert delta.mrr_cents == 2_000
    assert delta.new_subs_7d == 1
    assert delta.churned_subs_7d == 1
    assert delta.failed_charges_7d == -1


def test_format_delta_line_renders_useful_summary() -> None:
    delta = BriefingDelta(mrr_cents=200, new_subs_7d=1, churned_subs_7d=-1, failed_charges_7d=-1)
    line = format_delta_line(delta, prior_date="2026-05-04")
    assert "vs 2026-05-04" in line
    assert "MRR +$2" in line
    assert "new subs +1" in line


def test_format_delta_line_no_change_message() -> None:
    delta = BriefingDelta()
    line = format_delta_line(delta, prior_date="2026-05-04")
    assert "no change" in line


def test_briefing_cli_auto_saves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner.invoke(app, ["init", "--demo", "-y"])

    r = runner.invoke(app, ["briefing", "--raw"])
    assert r.exit_code == 0

    briefings = list((tmp_path / ".ronin" / "briefings").glob("*.json"))
    assert len(briefings) == 1
    payload = json.loads(briefings[0].read_text())
    assert payload["mrr_cents"] > 0


def test_briefing_history_subcommand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner.invoke(app, ["init", "--demo", "-y"])

    # Seed two prior snapshots so the table has rows
    save_snapshot(BriefingSnapshot(date="2026-04-27", mrr_cents=10_000, active_subs=4))
    save_snapshot(BriefingSnapshot(date="2026-05-04", mrr_cents=12_000, active_subs=5))

    r = runner.invoke(app, ["briefing", "--history"])
    assert r.exit_code == 0
    assert "Briefing history" in r.stdout
    assert "2026-04-27" in r.stdout
    assert "2026-05-04" in r.stdout


def test_briefing_includes_vs_last_week(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a prior snapshot exists, the briefing should show a delta line."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner.invoke(app, ["init", "--demo", "-y"])

    # Seed a prior snapshot with intentionally-different MRR so a delta surfaces
    save_snapshot(BriefingSnapshot(date="2026-05-04", mrr_cents=10_000, active_subs=4))

    r = runner.invoke(app, ["briefing", "--raw"])
    assert r.exit_code == 0
    assert "vs 2026-05-04" in r.stdout


def test_no_save_flag_skips_persistence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner.invoke(app, ["init", "--demo", "-y"])

    r = runner.invoke(app, ["briefing", "--raw", "--no-save"])
    assert r.exit_code == 0
    briefings_dir = tmp_path / ".ronin" / "briefings"
    assert not briefings_dir.exists() or not any(briefings_dir.iterdir())
