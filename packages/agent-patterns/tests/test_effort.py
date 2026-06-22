"""Tests for the effort / reasoning-budget knob (ronin_agent_patterns.effort).

Pure unit tests — no network, no config, no provider construction.
"""
from __future__ import annotations

import pytest

from ronin_agent_patterns.effort import (
    EFFORT_LEVELS,
    describe_effort,
    effort_to_params,
    normalize_effort,
)


# ---------- normalize_effort ----------


def test_levels_are_the_canonical_four() -> None:
    assert EFFORT_LEVELS == ["low", "medium", "high", "xhigh"]


@pytest.mark.parametrize("value", EFFORT_LEVELS)
def test_canonical_levels_round_trip(value: str) -> None:
    assert normalize_effort(value) == value


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("low", "low"),
        ("LOW", "low"),
        ("  low  ", "low"),
        ("lo", "low"),
        ("min", "low"),
        ("med", "medium"),
        ("medium", "medium"),
        ("MID", "medium"),
        ("default", "medium"),
        ("hi", "high"),
        ("HIGH", "high"),
        ("xhigh", "xhigh"),
        ("x-high", "xhigh"),
        ("max", "xhigh"),
        ("maximum", "xhigh"),
        ("ULTRA", "xhigh"),
    ],
)
def test_synonyms_canonicalize(raw: str, expected: str) -> None:
    assert normalize_effort(raw) == expected


@pytest.mark.parametrize("junk", [None, "", "   ", "banana", "veryhigh", "0", "high!", "supermax"])
def test_invalid_values_return_none(junk) -> None:
    assert normalize_effort(junk) is None


# ---------- effort_to_params: anthropic ----------


def test_anthropic_low_disables_thinking() -> None:
    # low → extended thinking stays off (empty merge = unchanged body).
    assert effort_to_params("anthropic", "low") == {}


def test_anthropic_budget_grows_with_effort() -> None:
    med = effort_to_params("anthropic", "medium")
    high = effort_to_params("anthropic", "high")
    xhigh = effort_to_params("anthropic", "xhigh")

    for params in (med, high, xhigh):
        assert params["thinking"]["type"] == "enabled"

    med_b = med["thinking"]["budget_tokens"]
    high_b = high["thinking"]["budget_tokens"]
    xhigh_b = xhigh["thinking"]["budget_tokens"]

    # Strictly increasing budget, all above Anthropic's 1024 minimum.
    assert 1024 <= med_b < high_b < xhigh_b
    assert (med_b, high_b, xhigh_b) == (4096, 8192, 16384)


# ---------- effort_to_params: openai ----------


@pytest.mark.parametrize(
    "effort,expected_effort",
    [("low", "low"), ("medium", "medium"), ("high", "high")],
)
def test_openai_reasoning_effort_present(effort: str, expected_effort: str) -> None:
    params = effort_to_params("openai", effort)
    assert params == {"reasoning_effort": expected_effort}


def test_openai_xhigh_clamped_to_high() -> None:
    # OpenAI's reasoning_effort enum only accepts low/medium/high — xhigh clamps.
    assert effort_to_params("openai", "xhigh") == {"reasoning_effort": "high"}


# ---------- effort_to_params: providers with NO reasoning knob ----------


@pytest.mark.parametrize("provider", ["groq", "cerebras", "ollama", "local", "gemini", "together", "fireworks", "openrouter", "custom"])
@pytest.mark.parametrize("effort", EFFORT_LEVELS)
def test_no_knob_providers_return_empty(provider: str, effort: str) -> None:
    assert effort_to_params(provider, effort) == {}


# ---------- effort_to_params: robustness ----------


def test_unknown_effort_is_inert_everywhere() -> None:
    # A non-canonical level never injects params, on any provider.
    assert effort_to_params("anthropic", "banana") == {}
    assert effort_to_params("openai", "") == {}
    assert effort_to_params("groq", "ultra") == {}  # 'ultra' is a synonym, not canonical


def test_provider_match_is_case_insensitive() -> None:
    assert effort_to_params("ANTHROPIC", "high")["thinking"]["budget_tokens"] == 8192
    assert effort_to_params("OpenAI", "medium") == {"reasoning_effort": "medium"}


# ---------- describe_effort ----------


@pytest.mark.parametrize("level", EFFORT_LEVELS)
def test_describe_effort_nonempty_for_each_level(level: str) -> None:
    desc = describe_effort(level)
    assert isinstance(desc, str) and desc.strip()


def test_describe_effort_unknown_does_not_raise() -> None:
    assert "banana" in describe_effort("banana")
