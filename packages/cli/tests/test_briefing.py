from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli.briefing import compute_briefing_data, render_briefing_md
from ronin_cli.config import RoninConfig
from ronin_cli.main import app
from ronin_cli.tools import build_tools


runner = CliRunner()


def test_compute_briefing_in_demo_mode_is_non_trivial() -> None:
    tools = build_tools(RoninConfig(demo_mode=True))
    data = compute_briefing_data(tools)

    # Revenue
    assert data.active_subs, "demo data should produce at least one active sub"
    assert data.mrr_cents > 0
    assert data.arr_cents == data.mrr_cents * 12

    # Demo has at least one recent new sub (created in the last 7 days)
    assert data.new_subs_7d, "demo data should show ≥1 new sub this week"
    # Demo has at least one churn within the last 7 days
    assert data.churned_subs_7d, "demo data should show ≥1 churn this week"
    # Demo has a past-due sub
    assert data.past_due_subs, "demo data should show ≥1 past-due sub"

    # Payments — at least one failed charge in demo, plus succeeded
    assert data.failed_charges_7d, "demo data should show ≥1 failed charge this week"
    assert data.succeeded_charges_7d, "demo data should show succeeded charges this week"

    # Engineering — at least one P0 in demo
    assert data.p0_open_issues, "demo data should show ≥1 P0 open"


def test_render_briefing_md_includes_key_sections() -> None:
    tools = build_tools(RoninConfig(demo_mode=True))
    data = compute_briefing_data(tools)
    md = render_briefing_md(data)

    assert "# Founder briefing" in md
    assert "## 💰 Revenue" in md
    assert "## 💳 Payments" in md
    assert "## 🛠 Engineering" in md
    assert "## ✅ Suggested action items" in md
    assert "MRR" in md
    # Includes actual numbers, not just headers
    assert "$" in md


def test_briefing_handles_missing_tools() -> None:
    """Empty toolset shouldn't crash — it should just yield an empty briefing."""
    data = compute_briefing_data([])
    md = render_briefing_md(data)
    assert "Founder briefing" in md
    assert data.mrr_cents == 0
    assert data.active_subs == []


def test_briefing_cli_demo_mode_runs_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner.invoke(app, ["init", "--demo", "-y"])

    r = runner.invoke(app, ["util", "briefing", "--raw"])
    assert r.exit_code == 0
    assert "Founder briefing" in r.stdout
    assert "Revenue" in r.stdout
    assert "MRR" in r.stdout


def test_briefing_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner.invoke(app, ["init", "--demo", "-y"])

    out = tmp_path / "briefing.md"
    r = runner.invoke(app, ["util", "briefing", "--out", str(out), "--raw"])
    assert r.exit_code == 0
    assert out.exists()
    content = out.read_text()
    assert "Founder briefing" in content
    assert "$" in content
