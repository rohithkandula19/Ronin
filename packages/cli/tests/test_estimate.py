"""Tests for the task estimate parser + cost projection."""
from __future__ import annotations

from ronin_cli.estimate import ESTIMATE_PROMPT, parse_estimate, project_cost


def test_parse_full() -> None:
    e = parse_estimate("Rationale…\nSIZE: L\nFILES: 7\nRISK: high")
    assert e.size == "L" and e.files == 7 and e.risk == "high"


def test_parse_case_insensitive() -> None:
    e = parse_estimate("size: xl\nfiles: 12\nrisk: Low")
    assert e.size == "XL" and e.files == 12 and e.risk == "low"


def test_parse_defaults_when_missing() -> None:
    e = parse_estimate("no markers here")
    assert e.size == "M" and e.files == 0 and e.risk == "medium"


def test_project_cost_scales_with_size() -> None:
    s = project_cost("S", "anthropic", "claude-sonnet-4-6")
    xl = project_cost("XL", "anthropic", "claude-sonnet-4-6")
    assert 0 < s < xl                      # bigger task → higher projected cost


def test_project_cost_free_provider() -> None:
    assert project_cost("L", "cerebras", "gpt-oss-120b") == 0.0


def test_project_cost_unknown_size_defaults_medium() -> None:
    assert project_cost("???", "anthropic", "claude-sonnet-4-6") == \
           project_cost("M", "anthropic", "claude-sonnet-4-6")


def test_prompt_has_required_lines() -> None:
    p = ESTIMATE_PROMPT.format(task="do a thing")
    assert "do a thing" in p and "SIZE:" in p and "FILES:" in p and "RISK:" in p
