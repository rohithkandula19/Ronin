"""Tests for the design-spec prompt builder."""
from __future__ import annotations

from ronin_cli.spec import SPEC_SECTIONS, spec_prompt


def test_spec_prompt_includes_feature_and_sections() -> None:
    p = spec_prompt("add a --json flag to the CLI")
    assert "add a --json flag to the CLI" in p
    for s in SPEC_SECTIONS:
        assert f"## {s}" in p
    assert "read-only" in p and "DESIGN SPEC" in p


def test_spec_prompt_custom_sections() -> None:
    p = spec_prompt("x", sections=["Goal", "Plan"])
    assert "## Goal" in p and "## Plan" in p
    assert "## Test plan" not in p


def test_sections_nonempty() -> None:
    assert len(SPEC_SECTIONS) >= 4 and all(SPEC_SECTIONS)
