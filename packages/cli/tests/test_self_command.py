"""Tests for the self-command interceptor — stop the chat from hallucinating
about ronin's own CLI commands."""
from __future__ import annotations

from ro_claude_kit_cli.self_command import detect_self_command, ronin_subcommands


def test_real_subcommands_are_discovered() -> None:
    subs = ronin_subcommands()
    # the features we shipped must be in the live set
    for cmd in ("duel", "kaizen", "dojo", "ghost", "consensus", "code"):
        assert cmd in subs


def test_detects_ronin_duel() -> None:
    assert detect_self_command("ronin duel -a gemini") == "ronin duel -a gemini"
    assert detect_self_command("ronin kaizen") == "ronin kaizen"


def test_normalizes_aliases() -> None:
    assert detect_self_command("ro duel --against gemini") == "ronin duel --against gemini"
    assert detect_self_command("csk kaizen") == "ronin kaizen"


def test_ignores_plain_chat() -> None:
    # normal conversation must NOT be intercepted
    assert detect_self_command("how do duels work in ronin?") is None
    assert detect_self_command("ronin is a cool name") is None  # 'is' isn't a subcommand
    assert detect_self_command("explain the duel feature") is None


def test_ignores_unknown_subcommand() -> None:
    assert detect_self_command("ronin list-opponents") is None   # the hallucinated one
    assert detect_self_command("ronin frobnicate") is None


def test_ignores_bare_word_and_empty() -> None:
    assert detect_self_command("duel") is None    # bare word could be real chat
    assert detect_self_command("ronin") is None    # no subcommand
    assert detect_self_command("") is None
