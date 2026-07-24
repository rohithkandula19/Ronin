from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli.briefing_anomaly import (
    Anomaly,
    detect_anomalies,
    render_anomalies_section,
)
from ronin_cli.briefing_history import BriefingSnapshot, save_snapshot
from ronin_cli.main import app


runner = CliRunner()


def _snap(date: str, **kwargs) -> BriefingSnapshot:
    return BriefingSnapshot(date=date, **kwargs)


# ---------- detection ----------

def test_no_anomalies_without_enough_history() -> None:
    """First three runs can't anchor a baseline — return nothing."""
    history = [_snap(f"2026-04-{i:02d}", mrr_cents=5_000) for i in (6, 13, 20)]
    current = _snap("2026-04-27", mrr_cents=999_999)  # huge spike
    assert detect_anomalies(history, current) == []


def test_no_anomalies_when_metric_is_constant() -> None:
    """Zero variance → can't flag deviations meaningfully."""
    history = [_snap(f"2026-04-{i:02d}", mrr_cents=5_000) for i in (6, 13, 20, 27)]
    current = _snap("2026-05-04", mrr_cents=5_000)
    assert detect_anomalies(history, current) == []


def test_detects_mrr_spike() -> None:
    history = [
        _snap("2026-04-06", mrr_cents=5_000),
        _snap("2026-04-13", mrr_cents=5_100),
        _snap("2026-04-20", mrr_cents=4_900),
        _snap("2026-04-27", mrr_cents=5_050),
    ]
    current = _snap("2026-05-04", mrr_cents=15_000)  # massive jump
    anomalies = detect_anomalies(history, current)
    assert anomalies
    mrr = next(a for a in anomalies if a.metric == "mrr_cents")
    assert mrr.direction == "up"
    assert mrr.z_score > 2
    assert "MRR" == mrr.label


def test_detects_mrr_dip() -> None:
    history = [_snap(f"2026-04-{i:02d}", mrr_cents=10_000) for i in (6, 13, 20, 27)]
    # Inject tiny variance so stdev > 0
    history[0].mrr_cents = 10_100
    history[1].mrr_cents = 9_900
    current = _snap("2026-05-04", mrr_cents=2_000)  # collapsed
    anomalies = detect_anomalies(history, current)
    mrr = next(a for a in anomalies if a.metric == "mrr_cents")
    assert mrr.direction == "down"
    assert mrr.z_score < -2


def test_detects_failed_charges_anomaly() -> None:
    history = [
        _snap("2026-04-06", failed_charges_7d=0),
        _snap("2026-04-13", failed_charges_7d=1),
        _snap("2026-04-20", failed_charges_7d=0),
        _snap("2026-04-27", failed_charges_7d=1),
    ]
    current = _snap("2026-05-04", failed_charges_7d=15)  # alarming spike
    anomalies = detect_anomalies(history, current)
    failed = next(a for a in anomalies if a.metric == "failed_charges_7d")
    assert failed.direction == "up"


def test_z_threshold_is_configurable() -> None:
    history = [
        _snap("2026-04-06", mrr_cents=5_000),
        _snap("2026-04-13", mrr_cents=5_100),
        _snap("2026-04-20", mrr_cents=4_900),
        _snap("2026-04-27", mrr_cents=5_050),
    ]
    current = _snap("2026-05-04", mrr_cents=5_400)  # mild rise, ~3σ

    # Strict threshold → no flag
    assert detect_anomalies(history, current, z_threshold=5.0) == []
    # Loose → flagged
    flagged = detect_anomalies(history, current, z_threshold=1.0)
    assert any(a.metric == "mrr_cents" for a in flagged)


# ---------- rendering ----------

def test_render_empty_section_returns_empty_string() -> None:
    assert render_anomalies_section([]) == ""


def test_render_includes_label_and_z() -> None:
    a = Anomaly(
        metric="mrr_cents",
        label="MRR",
        current=15_000,
        mean=5_000,
        stdev=100,
        z_score=100.0,
        direction="up",
        formatted_current="$150",
        formatted_mean="$50",
    )
    md = render_anomalies_section([a])
    assert "📊 Anomalies" in md
    assert "MRR" in md
    assert "$150" in md
    assert "$50" in md
    assert "z=100" in md
    assert "↑" in md


def test_render_direction_arrows_match() -> None:
    up = Anomaly(metric="x", label="X", current=10, mean=1, stdev=1, z_score=9,
                 direction="up", formatted_current="10", formatted_mean="1")
    down = Anomaly(metric="x", label="X", current=1, mean=10, stdev=1, z_score=-9,
                   direction="down", formatted_current="1", formatted_mean="10")
    assert "↑" in render_anomalies_section([up])
    assert "↓" in render_anomalies_section([down])
    assert "above" in render_anomalies_section([up])
    assert "below" in render_anomalies_section([down])


# ---------- CLI integration ----------

def test_briefing_cli_shows_anomalies_section_with_seeded_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seed 4 boring weeks then run briefing — current week's anomalies surface."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner.invoke(app, ["init", "--demo", "-y"])

    # Seed: 4 weeks where MRR was tiny and stable
    for i, date in enumerate(["2026-04-06", "2026-04-13", "2026-04-20", "2026-04-27"]):
        save_snapshot(_snap(date, mrr_cents=100 + i, active_subs=2, failed_charges_7d=0))

    r = runner.invoke(app, ["util", "briefing", "--raw"])
    assert r.exit_code == 0
    # Demo data has MRR=$334 (33400 cents) — huge spike over the seeded $1-ish
    assert "📊 Anomalies" in r.stdout
    assert "MRR" in r.stdout


def test_briefing_cli_skips_anomalies_when_history_too_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner.invoke(app, ["init", "--demo", "-y"])

    # Only 2 prior runs — not enough to anchor
    save_snapshot(_snap("2026-04-27", mrr_cents=100))
    save_snapshot(_snap("2026-05-04", mrr_cents=110))

    r = runner.invoke(app, ["util", "briefing", "--raw"])
    assert r.exit_code == 0
    assert "📊 Anomalies" not in r.stdout
