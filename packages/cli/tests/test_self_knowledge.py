"""The agent's front-door prompt must state ronin's own features accurately.

This began as a regression guard for the "does ronin have games?" bug — a model
with no self-knowledge denied that ronin had an arcade while `ronin play`
shipped one. The arcade has since been removed, and so have the two tests that
pinned its game count: a prompt that advertises a command which no longer exists
is the same bug pointing the other way.

What remains is the rule those tests were an instance of: the prompt states what
this program actually is, and a claim in it is checkable.
"""
from __future__ import annotations

from ronin_cli.code_mode import UNIFIED_SYSTEM


def test_the_prompt_no_longer_advertises_the_removed_arcade():
    """The other half of the original bug. A prompt naming a command that was
    deleted sends the user to a command that does not exist, which is worse than
    the denial this file was written to prevent — the model sounds certain."""
    low = UNIFIED_SYSTEM.lower()
    assert "ronin play" not in low
    assert "arcade" not in low


def test_unified_prompt_states_provider_agnostic_free_first():
    low = UNIFIED_SYSTEM.lower()
    assert "provider-agnostic" in low
    # names the free providers it can run on
    for provider in ("gemini", "groq", "cerebras", "ollama"):
        assert provider in low
