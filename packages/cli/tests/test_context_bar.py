"""Tests for the /context fullness bar."""
from __future__ import annotations

import re

from ronin_cli.code_mode import render_bar


def _counts(markup: str) -> tuple[int, int]:
    plain = re.sub(r"\[/?[^]]+\]", "", markup)
    return plain.count("▓"), plain.count("░")


def test_empty_bar() -> None:
    fill, empty = _counts(render_bar(0, 100, width=20))
    assert fill == 0 and empty == 20


def test_full_bar() -> None:
    fill, empty = _counts(render_bar(100, 100, width=20))
    assert fill == 20 and empty == 0


def test_half_bar() -> None:
    fill, empty = _counts(render_bar(50, 100, width=20))
    assert fill == 10 and empty == 10


def test_colour_warms_with_fill() -> None:
    assert "#9ece6a" in render_bar(10, 100)     # green when low
    assert "#e0af68" in render_bar(70, 100)     # amber mid
    assert "#f7768e" in render_bar(95, 100)     # red when nearly full


def test_handles_zero_total_and_overflow() -> None:
    assert "░" in render_bar(5, 0, width=10)            # no div-by-zero
    fill, _ = _counts(render_bar(200, 100, width=10))   # clamps at full
    assert fill == 10
