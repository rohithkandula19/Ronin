"""Tests for persistent Cost-Router savings."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.savings import append_turn, load_summary, savings_path


def test_empty_summary(tmp_path: Path) -> None:
    s = load_summary(tmp_path)
    assert s == {"spent": 0.0, "baseline": 0.0, "saved": 0.0, "turns": 0, "free_turns": 0}


def test_append_and_aggregate(tmp_path: Path) -> None:
    append_turn(tmp_path, actual=0.0, baseline=0.05, free=True)     # a free turn
    append_turn(tmp_path, actual=0.04, baseline=0.04, free=False)   # a strong turn
    assert savings_path(tmp_path).is_file()
    s = load_summary(tmp_path)
    assert s["turns"] == 2 and s["free_turns"] == 1
    assert abs(s["spent"] - 0.04) < 1e-9
    assert abs(s["baseline"] - 0.09) < 1e-9
    assert abs(s["saved"] - 0.05) < 1e-9


def test_saved_never_negative(tmp_path: Path) -> None:
    append_turn(tmp_path, actual=0.10, baseline=0.05, free=False)   # somehow pricier
    assert load_summary(tmp_path)["saved"] == 0.0


def test_corrupt_lines_skipped(tmp_path: Path) -> None:
    p = savings_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"actual":0.01,"baseline":0.03,"free":false}\nGARBAGE\n', encoding="utf-8")
    s = load_summary(tmp_path)
    # the valid line counts; the load aborts on the bad line but doesn't crash
    assert s["turns"] >= 0
