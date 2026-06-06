"""Tests for timezone formatting (deterministic via a fixed UTC instant)."""
from __future__ import annotations

import datetime

from ronin_cli.time_zones import format_in, world_clock

# a fixed reference instant: 2026-06-06 12:00 UTC
_UTC = datetime.datetime(2026, 6, 6, 12, 0, tzinfo=datetime.timezone.utc)


def test_format_in_utc() -> None:
    info = format_in(_UTC, "UTC")
    assert info["time"] == "12:00" and info["date"] == "2026-06-06"


def test_format_in_tokyo_offset() -> None:
    info = format_in(_UTC, "Asia/Tokyo")     # UTC+9 -> 21:00
    assert info["time"] == "21:00" and info["offset"] == "+09:00"


def test_format_in_new_york_summer() -> None:
    info = format_in(_UTC, "America/New_York")  # EDT in June -> UTC-4 -> 08:00
    assert info["time"] == "08:00" and info["offset"] == "-04:00"


def test_unknown_zone_is_none() -> None:
    assert format_in(_UTC, "Mars/Olympus") is None


def test_world_clock_skips_unknown() -> None:
    rows = world_clock(_UTC, ["UTC", "Bogus/Zone", "Asia/Tokyo"])
    assert [r["zone"] for r in rows] == ["UTC", "Asia/Tokyo"]
