"""Tests for the runaway-repetition guard."""
from __future__ import annotations

from ronin_agent_patterns.providers.repetition import (
    collapse_repetition,
    looks_repetitive,
)

# A realistic looped block (mirrors the gpt-oss-120b github-guide loop).
_BLOCK = (
    "Sure! I can work with any GitHub repository as long as the code is present "
    "on the machine where I'm running. Below is a short, step-by-step guide you "
    "can follow to bring a GitHub repo into the workspace I can see.\n\n"
)


def test_normal_text_is_not_flagged() -> None:
    prose = ("Here is a thorough explanation of how the parser works. " * 8)
    assert looks_repetitive(prose) is False


def test_looped_block_is_flagged() -> None:
    looped = _BLOCK * 4
    assert looks_repetitive(looped) is True


def test_short_text_never_flagged() -> None:
    assert looks_repetitive(_BLOCK) is False          # one copy, too short to judge


def test_dashes_only_not_flagged() -> None:
    # a wall of separators shouldn't trip the wordiness guard
    assert looks_repetitive(("-" * 80 + "\n") * 6) is False


def test_collapse_keeps_first_block_and_marks() -> None:
    looped = _BLOCK * 5
    out = collapse_repetition(looped)
    assert "ronin trimmed a runaway repetition loop" in out
    assert out.count("Sure! I can work with any GitHub repository") == 1
    assert len(out) < len(looped)


def test_collapse_leaves_clean_text_untouched() -> None:
    clean = "A normal, non-looping answer that says one thing once."
    assert collapse_repetition(clean) == clean
