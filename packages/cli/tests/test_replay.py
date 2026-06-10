"""Tests for replay — transcript → turns parsing."""
from __future__ import annotations

from ronin_cli.replay import Turn, session_to_markdown, transcript_to_turns


def test_basic_pairing() -> None:
    turns = transcript_to_turns(["USER: hi", "ASSISTANT: hello", "USER: bye"])
    assert turns == [Turn("user", "hi"), Turn("assistant", "hello"), Turn("user", "bye")]


def test_multiline_folds_into_previous() -> None:
    turns = transcript_to_turns(["USER: fix this", "ASSISTANT: sure", "here is line two"])
    assert len(turns) == 2
    assert turns[1].role == "assistant"
    assert turns[1].text == "sure\nhere is line two"


def test_empty() -> None:
    assert transcript_to_turns([]) == []


def test_leading_orphan_line_ignored() -> None:
    # a stray line before any USER/ASSISTANT has nowhere to fold → dropped
    turns = transcript_to_turns(["stray", "USER: real"])
    assert turns == [Turn("user", "real")]


def test_session_to_markdown() -> None:
    turns = [Turn("user", "fix the bug"), Turn("assistant", "done"),
             Turn("user", "thanks")]
    md = session_to_markdown(turns, title="My Fix")
    assert md.startswith("# My Fix")
    assert "## Turn 1 — you" in md and "## Turn 2 — you" in md
    assert "**ronin:**" in md and "fix the bug" in md
    assert md.endswith("\n")
