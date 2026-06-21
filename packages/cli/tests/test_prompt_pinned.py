"""Tests for the PURE layout helpers in ``prompt_pinned``.

Only the deterministic, terminal-free helpers are tested here: ``format_left``
and ``status_bar``. The interactive ``pinned_prompt`` needs a live TTY and is
intentionally not exercised.
"""
from __future__ import annotations

import pytest

from ronin_cli.prompt_pinned import format_left, status_bar


# ---------------------------------------------------------------- format_left --
def test_format_left_basic() -> None:
    assert format_left(42) == "42% ctx"
    assert format_left(0) == "0% ctx"
    assert format_left(100) == "100% ctx"


def test_format_left_clamps_out_of_range() -> None:
    assert format_left(150) == "100% ctx"
    assert format_left(-5) == "0% ctx"


def test_format_left_coerces_non_int() -> None:
    # A stray non-int from the caller must not blow up the bar.
    assert format_left(None) == "0% ctx"  # type: ignore[arg-type]
    assert format_left("oops") == "0% ctx"  # type: ignore[arg-type]
    assert format_left(37.9) == "37% ctx"  # type: ignore[arg-type]


# ----------------------------------------------------------------- status_bar --
def test_status_bar_contains_model_and_ctx() -> None:
    bar = status_bar("zai-glm-4.7", 42, "/repo", width=80)
    assert "zai-glm-4.7" in bar
    assert "42% ctx" in bar
    assert "/repo" in bar


def test_status_bar_uses_separators() -> None:
    bar = status_bar("m", 10, "/x", width=80)
    assert bar == "m · 10% ctx · /x"


def test_status_bar_never_exceeds_width() -> None:
    long_cwd = "/Users/someone/very/deep/nested/path/to/a/project/repo/cli/pkg"
    for w in (10, 20, 30, 40, 60, 80, 120):
        bar = status_bar("zai-glm-4.7", 42, long_cwd, width=w)
        assert len(bar) <= w, f"width {w}: {len(bar)} > {w} ({bar!r})"


def test_status_bar_truncates_cwd_first_keeping_model_and_ctx() -> None:
    # Wide enough for the head ("model · NN% ctx") but not the full cwd → the
    # model and the gauge must survive; the cwd is what gets shortened.
    long_cwd = "/Users/someone/very/deep/nested/path/to/project/cli/pkg"
    bar = status_bar("glm-4.7", 55, long_cwd, width=30)
    assert len(bar) <= 30
    assert "glm-4.7" in bar
    assert "55% ctx" in bar
    # the cwd is truncated head-first, so it carries the leading ellipsis…
    assert "…" in bar
    # …and preserves the meaningful tail of the path.
    assert bar.endswith("pkg")


def test_status_bar_drops_cwd_when_no_room() -> None:
    # Width fits "model · NN% ctx" but leaves no room for "· <cwd>".
    head = "model · 42% ctx"
    bar = status_bar("model", 42, "/some/long/path", width=len(head) + 1)
    assert bar == head
    assert "model" in bar and "42% ctx" in bar


def test_status_bar_empty_cwd() -> None:
    bar = status_bar("model", 7, "", width=80)
    assert bar == "model · 7% ctx"


def test_status_bar_degenerate_narrow_width() -> None:
    # Absurdly narrow: still must not exceed width and must not raise.
    bar = status_bar("zai-glm-4.7", 42, "/repo", width=5)
    assert len(bar) <= 5


def test_status_bar_exact_fit() -> None:
    # Exactly fits → returned whole, untouched.
    bar = status_bar("m", 1, "/a", width=len("m · 1% ctx · /a"))
    assert bar == "m · 1% ctx · /a"


def test_status_bar_default_width_is_80() -> None:
    bar = status_bar("m" * 200, 50, "/x" * 200)  # default width
    assert len(bar) <= 80
