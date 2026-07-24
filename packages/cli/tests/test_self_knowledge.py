"""The agent's front-door prompt must state ronin's own features accurately.

Regression guard for the "does ronin have games?" bug: a free model with no
self-knowledge answered that ronin has no games, when `ronin play` ships a
31-game arcade. The UNIFIED_SYSTEM prompt now carries those facts; these tests
keep them present and in sync with the real game registry.
"""
from __future__ import annotations

import re

from ronin_cli.code_mode import UNIFIED_SYSTEM
from ronin_arcade.games import GAMES


def test_unified_prompt_knows_about_the_arcade():
    assert "ronin play" in UNIFIED_SYSTEM
    assert "games" in UNIFIED_SYSTEM.lower()


def test_unified_prompt_game_count_matches_registry():
    # The number stated in the prompt must equal the real number of games, so
    # adding/removing a game without updating the prompt fails CI.
    m = re.search(r"arcade of (\d+) free terminal\s+games", UNIFIED_SYSTEM)
    assert m, "prompt should state the arcade game count"
    assert int(m.group(1)) == len(GAMES) == 31


def test_unified_prompt_states_provider_agnostic_free_first():
    low = UNIFIED_SYSTEM.lower()
    assert "provider-agnostic" in low
    # names the free providers it can run on
    for provider in ("gemini", "groq", "cerebras", "ollama"):
        assert provider in low
