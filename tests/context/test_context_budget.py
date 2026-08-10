"""The truncator, with the effort spent on the cases where a sloppier one lies.

The interesting failures are not "long text got shorter". They are: content that
already fits being reformatted anyway, a marker that names the wrong count, a budget
smaller than the marker, a single line with nothing to split on, and a missing
trailing newline turning into a phantom one.
"""

from __future__ import annotations

import pytest

from ronin.context.budget import (
    CHARS_PER_TOKEN,
    ClampMode,
    OutputBudget,
    char_marker,
    clamp,
    estimate_tokens,
    line_marker,
)

LOG = "".join(f"line {index:04d} of the build log\n" for index in range(300))


def test_content_under_budget_is_returned_byte_identical() -> None:
    text = "one\ntwo\nthree\n"
    result = clamp(text, budget=OutputBudget(remaining_chars=1000))
    assert result.text == text
    assert result.mode is ClampMode.NONE
    assert not result.truncated
    assert result.marker == ""


def test_content_exactly_at_the_budget_is_untouched() -> None:
    text = "abcde"
    assert clamp(text, budget=OutputBudget(remaining_chars=5)).text == text


def test_the_marker_names_the_true_elided_line_count() -> None:
    result = clamp(LOG, budget=OutputBudget(remaining_chars=400))
    kept = [line for line in result.text.splitlines() if not line.startswith("[...")]
    assert result.marker == line_marker(300 - len(kept))
    assert result.elided_lines == 300 - len(kept)
    assert line_marker(result.elided_lines) in result.text


def test_head_and_tail_are_both_kept_and_the_split_is_sixty_forty() -> None:
    result = clamp(LOG, budget=OutputBudget(remaining_chars=600))
    head, _, tail = result.text.partition(result.marker)
    assert head.startswith("line 0000")
    assert tail.strip().endswith("line 0299 of the build log")
    # 60/40 of what survives, within one line of rounding.
    assert len(head) > len(tail)
    assert abs(len(head) / (len(head) + len(tail)) - 0.6) < 0.1


def test_clamping_is_reproducible_for_identical_input() -> None:
    budget = OutputBudget(remaining_chars=777)
    assert clamp(LOG, budget=budget) == clamp(LOG, budget=budget)


def test_the_result_fits_the_budget() -> None:
    for size in (120, 300, 512, 1024, 4096):
        result = clamp(LOG, budget=OutputBudget(remaining_chars=size))
        assert len(result.text) <= size, size


def test_a_budget_smaller_than_the_marker_returns_the_marker_alone() -> None:
    """Silently returning nothing would hide the truncation entirely."""
    result = clamp(LOG, budget=OutputBudget(remaining_chars=5))
    assert result.mode is ClampMode.MARKER_ONLY
    assert result.text == line_marker(300)
    assert result.elided_chars == len(LOG)


def test_a_single_enormous_line_falls_back_to_characters_and_says_so() -> None:
    blob = "x" * 10_000
    result = clamp(blob, budget=OutputBudget(remaining_chars=200))
    assert result.mode is ClampMode.CHARS
    assert "no line breaks to split on" in result.text
    assert result.marker == char_marker(result.elided_chars)
    assert len(result.text) <= 200
    assert result.text.startswith("x")
    assert result.text.endswith("x")


def test_missing_trailing_newline_does_not_gain_one() -> None:
    text = "\n".join(f"line {index}" for index in range(200))
    assert not text.endswith("\n")
    result = clamp(text, budget=OutputBudget(remaining_chars=300))
    assert not result.text.endswith("\n")


def test_a_marker_only_result_never_claims_zero_elided_lines() -> None:
    result = clamp(LOG, budget=OutputBudget(remaining_chars=1))
    assert result.elided_lines == 300


def test_the_line_ceiling_binds_even_when_characters_fit() -> None:
    text = "".join("a\n" for _ in range(500))
    result = clamp(text, budget=OutputBudget(remaining_chars=100_000, remaining_lines=50))
    assert result.truncated
    assert len(result.text.splitlines()) <= 50


def test_a_zero_line_ceiling_yields_the_marker_alone() -> None:
    result = clamp(LOG, budget=OutputBudget(remaining_chars=100_000, remaining_lines=0))
    assert result.mode is ClampMode.MARKER_ONLY


@pytest.mark.parametrize(
    ("text", "lines"),
    [("", 0), ("a", 1), ("a\n", 1), ("a\nb", 2), ("a\nb\n", 2)],
)
def test_spend_counts_lines_the_way_a_human_does(text: str, lines: int) -> None:
    budget = OutputBudget(remaining_chars=100, remaining_lines=10).spend(text)
    assert budget.remaining_lines == 10 - lines
    assert budget.remaining_chars == 100 - len(text)


def test_spend_floors_at_zero_rather_than_going_negative() -> None:
    budget = OutputBudget(remaining_chars=3, remaining_lines=1).spend("aaaaaaa\nbbb\n")
    assert budget.remaining_chars == 0
    assert budget.remaining_lines == 0


@pytest.mark.parametrize("fraction", [0.0, 1.0, -0.5, 1.5])
def test_an_all_head_or_all_tail_split_is_rejected(fraction: float) -> None:
    with pytest.raises(ValueError, match="head_fraction"):
        OutputBudget(remaining_chars=100, head_fraction=fraction)


def test_a_negative_budget_is_rejected() -> None:
    with pytest.raises(ValueError, match="remaining_chars"):
        OutputBudget(remaining_chars=-1)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", 0), ("abcd", 1), ("abcde", 2), ("a" * 4000, 1000)],
)
def test_the_token_estimator_is_chars_over_four_rounded_up(text: str, expected: int) -> None:
    assert CHARS_PER_TOKEN == 4
    assert estimate_tokens(text) == expected
