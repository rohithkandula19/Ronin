"""Tests for the cron matcher (pure, offline)."""
from __future__ import annotations

from datetime import datetime

import pytest

from ronin_cli.cron_match import (
    cron_matches,
    is_valid_cron,
    normalize,
    parse_cron,
)


def test_normalize_aliases() -> None:
    assert normalize("@daily") == "0 0 * * *"
    assert normalize("@hourly") == "0 * * * *"
    assert normalize("0 9 * * *") == "0 9 * * *"  # passthrough


def test_is_valid_cron() -> None:
    assert is_valid_cron("@daily")
    assert is_valid_cron("*/15 9-17 * * 1-5")
    assert is_valid_cron("0 0 1 1 *")
    assert not is_valid_cron("bogus")
    assert not is_valid_cron("0 0 * *")          # 4 fields
    assert not is_valid_cron("99 0 * * *")       # minute out of range
    assert not is_valid_cron("0 0 * * 1-9")      # dow out of range
    assert not is_valid_cron("0 5-1 * * *")      # inverted range


def test_parse_cron_star() -> None:
    minutes, hours, doms, months, dows = parse_cron("* * * * *")
    assert minutes == set(range(60))
    assert hours == set(range(24))
    assert doms == set(range(1, 32))
    assert months == set(range(1, 13))
    assert dows == set(range(7))


def test_parse_cron_step_and_range() -> None:
    minutes, hours, *_ = parse_cron("*/15 9-17 * * *")
    assert minutes == {0, 15, 30, 45}
    assert hours == set(range(9, 18))


def test_parse_cron_dow_seven_is_sunday() -> None:
    *_, dows = parse_cron("0 0 * * 7")
    assert dows == {0}


def test_matches_daily_midnight() -> None:
    assert cron_matches("@daily", datetime(2026, 6, 17, 0, 0))
    assert not cron_matches("@daily", datetime(2026, 6, 17, 0, 1))
    assert not cron_matches("@daily", datetime(2026, 6, 17, 9, 0))


def test_matches_weekday_window() -> None:
    # 0 9 * * 1-5  → 09:00 Mon-Fri. 2026-06-15 is a Monday, 06-14 a Sunday.
    assert cron_matches("0 9 * * 1-5", datetime(2026, 6, 15, 9, 0))   # Monday
    assert cron_matches("0 9 * * 1-5", datetime(2026, 6, 19, 9, 0))   # Friday
    assert not cron_matches("0 9 * * 1-5", datetime(2026, 6, 14, 9, 0))  # Sunday
    assert not cron_matches("0 9 * * 1-5", datetime(2026, 6, 15, 8, 0))  # wrong hour


def test_matches_every_15_minutes() -> None:
    assert cron_matches("*/15 * * * *", datetime(2026, 6, 17, 13, 30))
    assert not cron_matches("*/15 * * * *", datetime(2026, 6, 17, 13, 31))


def test_dom_dow_or_semantics() -> None:
    # 0 0 1 * 1 → fires on the 1st OR on any Monday (Vixie-cron OR rule).
    # 2026-06-01 is a Monday; pick a non-Monday 1st and a non-1st Monday.
    assert cron_matches("0 0 1 * 1", datetime(2026, 7, 1, 0, 0))   # 1st (Wednesday)
    assert cron_matches("0 0 1 * 1", datetime(2026, 6, 8, 0, 0))   # Monday, not 1st
    assert not cron_matches("0 0 1 * 1", datetime(2026, 6, 9, 0, 0))  # neither


def test_invalid_expr_raises_on_parse() -> None:
    with pytest.raises(ValueError):
        parse_cron("nope")
