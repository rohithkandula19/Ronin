"""Tests for ``ronin stats`` — the usage dashboard.

The aggregation / streak / heatmap / comparison maths is a pure deterministic
core (dates are passed in), so no clock mocking is needed. The impure loaders
and renderer are exercised against a ``RONIN_HOME``-style temp dir and a
no-output Console.
"""
from __future__ import annotations

from pathlib import Path

from ronin_cli import stats
from ronin_cli.stats import (
    aggregate,
    fun_comparison,
    heatmap_cells,
    peak_hour,
    streaks,
)


# --------------------------------------------------------------------------- #
# streaks — a 3-day run plus a gap
# --------------------------------------------------------------------------- #
def test_streaks_three_day_run_current_and_longest() -> None:
    dates = ["2026-06-17", "2026-06-18", "2026-06-19"]
    current, longest = streaks(dates, today="2026-06-19")
    assert longest == 3
    assert current == 3                       # run ends today


def test_streaks_gap_breaks_longest_but_keeps_recent_current() -> None:
    # a 3-day run, then a gap, then a fresh 2-day run ending today
    dates = ["2026-06-01", "2026-06-02", "2026-06-03",   # old 3-run
             "2026-06-18", "2026-06-19"]                  # recent 2-run
    current, longest = streaks(dates, today="2026-06-19")
    assert longest == 3                       # the old run is the longest anywhere
    assert current == 2                       # only the run ending today counts


def test_streaks_current_zero_when_today_after_a_gap() -> None:
    # last activity was days ago → the current streak is already broken
    dates = ["2026-06-10", "2026-06-11", "2026-06-12"]
    current, longest = streaks(dates, today="2026-06-19")
    assert longest == 3
    assert current == 0


def test_streaks_current_counts_yesterday() -> None:
    # a streak ending yesterday is still alive (today isn't over)
    current, _ = streaks(["2026-06-17", "2026-06-18"], today="2026-06-19")
    assert current == 2


def test_streaks_empty_is_zero_zero() -> None:
    assert streaks([], today="2026-06-19") == (0, 0)


def test_streaks_dedupes_and_ignores_bad_dates() -> None:
    dates = ["2026-06-18", "2026-06-18", "garbage", "2026-06-19", "2026-06-19"]
    current, longest = streaks(dates, today="2026-06-19")
    assert longest == 2
    assert current == 2


def test_streaks_without_today_uses_latest_day() -> None:
    # no `today` → current is the run ending on the most recent active day
    current, longest = streaks(["2026-06-17", "2026-06-18", "2026-06-19"])
    assert current == 3
    assert longest == 3


# --------------------------------------------------------------------------- #
# peak_hour
# --------------------------------------------------------------------------- #
def test_peak_hour_most_frequent() -> None:
    assert peak_hour([20, 20, 9, 20]) == 20


def test_peak_hour_tie_breaks_to_earlier() -> None:
    # 8 and 22 both appear twice → the earlier hour wins (deterministic)
    assert peak_hour([8, 22, 8, 22]) == 8


def test_peak_hour_empty_is_zero() -> None:
    assert peak_hour([]) == 0


def test_peak_hour_ignores_out_of_range() -> None:
    assert peak_hour([99, -1, 13, 13]) == 13


# --------------------------------------------------------------------------- #
# aggregate — favorite model + token totals
# --------------------------------------------------------------------------- #
def test_aggregate_favorite_model_and_totals() -> None:
    records = [
        {"timestamp": "2026-06-17T20:01:00+00:00", "provider": "anthropic",
         "model": "claude-sonnet-4-6", "input_tokens": 100, "output_tokens": 50,
         "command": "code"},
        {"timestamp": "2026-06-18T09:30:00+00:00", "provider": "anthropic",
         "model": "claude-sonnet-4-6", "input_tokens": 200, "output_tokens": 80,
         "command": "ask"},
        {"timestamp": "2026-06-18T21:15:00+00:00", "provider": "openai",
         "model": "gpt-4o-mini", "input_tokens": 40, "output_tokens": 10,
         "command": "code"},
    ]
    agg = aggregate(records)
    assert agg["favorite_model"] == "claude-sonnet-4-6"     # appears twice
    assert agg["input_tokens"] == 340
    assert agg["output_tokens"] == 140
    assert agg["total_tokens"] == 480
    assert agg["by_model"]["claude-sonnet-4-6"] == 2
    assert agg["favorite_command"] == "code"               # 2x code vs 1x ask
    assert agg["active_dates"] == ["2026-06-17", "2026-06-18"]
    assert agg["date_counts"]["2026-06-18"] == 2


def test_aggregate_empty_is_honest_not_a_crash() -> None:
    agg = aggregate([])
    assert agg["total_tokens"] == 0
    assert agg["favorite_model"] is None
    assert agg["active_dates"] == []
    assert agg["records"] == 0


def test_aggregate_tolerates_garbage_rows() -> None:
    records = [
        {"timestamp": "bad", "input_tokens": "120", "output_tokens": None, "model": "m"},
        "not-a-dict",
        {"model": "", "input_tokens": 5, "output_tokens": 5},
    ]
    agg = aggregate(records)            # must not raise
    assert agg["input_tokens"] == 125  # "120" coerced + 5
    assert agg["total_tokens"] == 130


def test_aggregate_hours_extracted_from_timestamp() -> None:
    records = [
        {"timestamp": "2026-06-18T20:00:00+00:00", "model": "m",
         "input_tokens": 1, "output_tokens": 1},
        {"timestamp": "2026-06-18T20:30:00+00:00", "model": "m",
         "input_tokens": 1, "output_tokens": 1},
        {"timestamp": "2026-06-18T09:00:00+00:00", "model": "m",
         "input_tokens": 1, "output_tokens": 1},
    ]
    agg = aggregate(records)
    assert peak_hour(agg["hours"]) == 20


# --------------------------------------------------------------------------- #
# heatmap_cells — shape + intensity bucketing
# --------------------------------------------------------------------------- #
def test_heatmap_cells_shape_is_seven_by_weeks() -> None:
    grid = heatmap_cells({}, today="2026-06-19", weeks=20)
    assert len(grid) == 7                       # 7 weekday rows
    assert all(len(col) == 20 for col in grid)  # weeks columns each
    # all-empty input → every cell level 0
    assert all(cell == 0 for row in grid for cell in row)


def test_heatmap_cells_lights_the_right_day() -> None:
    # an active day inside the window must be non-zero; today=2026-06-19 (Fri)
    grid = heatmap_cells({"2026-06-19": 3}, today="2026-06-19", weeks=4)
    # 2026-06-19 is a Friday → Sun=0..Sat=6 → row 5
    assert grid[5][-1] >= 1                      # rightmost column, Friday row
    lit = sum(1 for row in grid for c in row if c > 0)
    assert lit == 1                             # exactly one lit cell


def test_heatmap_cells_intensity_buckets_spread_across_levels() -> None:
    # a spread of counts must map to a spread of levels, busiest → level 4
    counts = {
        "2026-06-15": 1,    # light
        "2026-06-16": 3,
        "2026-06-17": 8,
        "2026-06-18": 20,
        "2026-06-19": 60,   # heaviest → must be level 4
    }
    grid = heatmap_cells(counts, today="2026-06-19", weeks=4)
    levels = {c for row in grid for c in row if c > 0}
    assert 4 in levels                          # the busiest day reaches the top band
    assert len(levels) >= 3                     # genuinely spread, not all one colour
    assert levels <= {1, 2, 3, 4}


def test_heatmap_cells_future_days_stay_zero() -> None:
    # a date after `today` must never light up (no leaking the future)
    grid = heatmap_cells({"2026-06-30": 99}, today="2026-06-19", weeks=4)
    assert all(c == 0 for row in grid for c in row)


def test_heatmap_cells_all_single_hits_are_top_level() -> None:
    # when every active day has exactly one hit, they should all read as "active"
    counts = {"2026-06-17": 1, "2026-06-18": 1, "2026-06-19": 1}
    grid = heatmap_cells(counts, today="2026-06-19", weeks=4)
    lit = [c for row in grid for c in row if c > 0]
    assert len(lit) == 3
    assert all(c == 4 for c in lit)


# --------------------------------------------------------------------------- #
# fun_comparison
# --------------------------------------------------------------------------- #
def test_fun_comparison_picks_best_reference_and_multiple() -> None:
    # 64M tokens vs Harry Potter (~1.5M) → ~42x, the largest reference exceeded
    line = fun_comparison(64_000_000)
    assert "Lord of the Rings" in line          # 64M > LotR (2.1M), the biggest book set
    assert "x" in line


def test_fun_comparison_animal_farm_band() -> None:
    line = fun_comparison(80_000)               # ~2x Animal Farm (40k), under Gatsby (62k)? no -> Gatsby
    # 80k exceeds Gatsby (62k) but not Hobbit (130k) → Gatsby is the pick
    assert "Gatsby" in line
    assert "x" in line


def test_fun_comparison_small_total_is_a_fraction() -> None:
    line = fun_comparison(10)                    # below the smallest reference (a tweet, 50)
    assert "%" in line


def test_fun_comparison_zero_is_empty_state() -> None:
    line = fun_comparison(0)
    assert "no tokens" in line.lower()


def test_fun_comparison_returns_a_string() -> None:
    for n in (0, 1, 500, 40_000, 1_000_000, 5_000_000_000):
        assert isinstance(fun_comparison(n), str)


# --------------------------------------------------------------------------- #
# impure layer: load_records + summarize + render (temp dir, no-output console)
# --------------------------------------------------------------------------- #
def test_load_records_tolerates_missing(tmp_path: Path) -> None:
    # a usage path that doesn't exist + no sessions dir → two empty lists, no crash
    usage, sessions = stats.load_records(usage_path=tmp_path / "nope.jsonl", root=tmp_path)
    assert usage == []
    assert isinstance(sessions, list)


def test_load_records_reads_usage_jsonl(tmp_path: Path) -> None:
    p = tmp_path / "usage.jsonl"
    p.write_text(
        '{"timestamp": "2026-06-18T20:00:00+00:00", "command": "code", '
        '"provider": "anthropic", "model": "claude-sonnet-4-6", '
        '"input_tokens": 100, "output_tokens": 40, "cost_usd": 0.001}\n'
        "\n"                                  # blank line tolerated
        "{ broken json\n",                    # half-written line tolerated
        encoding="utf-8",
    )
    usage, _ = stats.load_records(usage_path=p, root=tmp_path)
    assert len(usage) == 1
    assert usage[0]["model"] == "claude-sonnet-4-6"


def test_summarize_combines_usage_and_sessions() -> None:
    usage = [
        {"timestamp": "2026-06-18T20:00:00+00:00", "model": "claude-sonnet-4-6",
         "provider": "anthropic", "input_tokens": 1000, "output_tokens": 500,
         "command": "code"},
    ]
    sessions = [
        {"id": "a", "date": "2026-06-18", "turns": 3, "messages": 6},
        {"id": "b", "date": "2026-06-19", "turns": 1, "messages": 2},
    ]
    snap = stats.summarize(usage, sessions, today="2026-06-19")
    assert snap["sessions"] == 2
    assert snap["messages"] == 8
    assert snap["total_tokens"] == 1500
    assert snap["active_days"] == 2            # 06-18 and 06-19
    assert snap["current_streak"] == 2         # consecutive, ends today
    assert snap["has_data"] is True


def test_render_empty_state_smoke(tmp_path: Path) -> None:
    from rich.console import Console

    snap = stats.summarize([], [], today="2026-06-19")
    out_file = tmp_path / "out.txt"
    console = Console(file=open(out_file, "w", encoding="utf-8"), width=100)
    stats.render(console, records=snap)
    console.file.close()
    text = out_file.read_text()
    assert "no activity logged yet" in text
    assert "ronin code" in text


def test_render_populated_smoke(tmp_path: Path) -> None:
    from rich.console import Console

    usage = [
        {"timestamp": "2026-06-18T20:00:00+00:00", "model": "claude-sonnet-4-6",
         "provider": "anthropic", "input_tokens": 50_000, "output_tokens": 20_000,
         "command": "code"},
        {"timestamp": "2026-06-19T21:00:00+00:00", "model": "claude-sonnet-4-6",
         "provider": "anthropic", "input_tokens": 30_000, "output_tokens": 10_000,
         "command": "code"},
    ]
    sessions = [{"id": "a", "date": "2026-06-18", "turns": 4, "messages": 8}]
    snap = stats.summarize(usage, sessions, today="2026-06-19")
    out_file = tmp_path / "out.txt"
    console = Console(file=open(out_file, "w", encoding="utf-8"), width=120)
    stats.render(console, records=snap)
    console.file.close()
    text = out_file.read_text()
    assert "SESSIONS" in text
    assert "TOTAL TOKENS" in text
    assert "Activity" in text                  # heatmap section header
    assert "ronin stats" in text
