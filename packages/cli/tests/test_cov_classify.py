"""Edge-case coverage for routing.classify (the Cost Router's task classifier)."""
from __future__ import annotations

import pytest

from ronin_cli.routing import classify


@pytest.mark.parametrize("msg", [
    "fix the bug in auth.py",
    "refactor the database layer",
    "implement a retry helper",
    "debug this stack trace",
    "write a test for the parser",
    "review my changes",
    "optimize the query",
    "migrate to the new API",
    "create a new endpoint",
    "explain how the router works",
    "investigate the flaky test",
])
def test_keyword_turns_are_complex(msg: str) -> None:
    assert classify(msg) == "complex"


@pytest.mark.parametrize("msg", [
    "hi",
    "thanks!",
    "what time is it",
    "who are you",
    "cool",
])
def test_short_chitchat_is_simple(msg: str) -> None:
    assert classify(msg) == "simple"


def test_file_mention_is_complex() -> None:
    assert classify("look at @main.py") == "complex"


def test_code_block_is_complex() -> None:
    assert classify("what does ```x=1``` do") == "complex"


def test_long_message_is_complex() -> None:
    assert classify("please " * 50) == "complex"   # >220 chars


def test_path_prefix_is_complex() -> None:
    assert classify("/Users/me/project do something") == "complex"
    assert classify("~/project explain") == "complex"


def test_path_in_first_word_is_complex() -> None:
    assert classify("src/main.py needs work") == "complex"


def test_case_insensitive_keywords() -> None:
    assert classify("FIX THE BUG") == "complex"
    assert classify("Refactor This") == "complex"
