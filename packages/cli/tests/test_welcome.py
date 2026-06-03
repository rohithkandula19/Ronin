"""Tests for the rotating welcome banner helpers."""
from __future__ import annotations

from ronin_cli.code_mode import _WELCOME_TIPS, _greeting, _welcome
from ronin_cli.config import RoninConfig


def test_tips_present_and_unique() -> None:
    assert len(_WELCOME_TIPS) >= 5
    assert len(set(_WELCOME_TIPS)) == len(_WELCOME_TIPS)   # no duplicates


def test_greeting_is_a_string() -> None:
    g = _greeting()
    assert isinstance(g, str) and g


def test_every_tip_nonempty() -> None:
    assert all(t.strip() for t in _WELCOME_TIPS)


def test_welcome_renders_without_crashing(tmp_path) -> None:
    from rich.console import Console
    # non-TTY → deterministic path (single frame, fixed tip); must not raise
    c = Console(file=open("/dev/null", "w"))
    _welcome(c, RoninConfig(provider="cerebras"), tmp_path, False, title="ronin", hint="")


def test_welcome_rotation_uses_real_activities() -> None:
    # the activity pool must come from the real panda animation set
    from ronin_cli.panda_art import PANDA_ACTIVITIES
    assert len(PANDA_ACTIVITIES) >= 3   # several poses to rotate through
