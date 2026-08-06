"""Contracts for human-centered interactive delivery."""
from __future__ import annotations

import io
from unittest.mock import patch

from rich.console import Console

from ronin_agent_patterns import FakeProvider, LLMResponse
from ronin_cli.code_mode import run_code_agent
from ronin_cli.config import RoninConfig
from ronin_cli.presence import detect_conversation_cue, interaction_system_block, normalize_interaction_style


def test_presence_style_normalizes_to_a_safe_default() -> None:
    assert normalize_interaction_style("SUPPORTIVE") == "supportive"
    assert normalize_interaction_style("unknown") == "balanced"


def test_presence_uses_only_explicit_current_turn_cues() -> None:
    cue = detect_conversation_cue("I am stuck and frustrated with this failure")
    assert cue is not None and cue.kind == "friction"
    assert detect_conversation_cue("Please update the retry policy") is None


def test_presence_prompt_is_warm_but_honest_and_non_persistent() -> None:
    block = interaction_system_block("supportive", checkins=True, task="This is urgent and I am stuck")
    assert "never claim to be human" in block
    assert "Never retain inferred feelings" in block
    assert "CURRENT-TURN DELIVERY CUE" in block


def test_quiet_presence_does_not_add_a_delivery_cue() -> None:
    block = interaction_system_block("quiet", checkins=True, task="I am frustrated")
    assert "CURRENT-TURN DELIVERY CUE" not in block


def test_presence_is_injected_only_for_person_facing_sessions(tmp_path) -> None:
    config = RoninConfig(provider="cerebras", interaction_style="supportive")
    interactive = FakeProvider(responses=[LLMResponse(text="I will start with the failure.", stop_reason="end_turn")])
    console = Console(file=io.StringIO(), force_terminal=False)
    with patch("ronin_cli.code_mode.build_provider", return_value=interactive):
        run_code_agent(config, "I am stuck and this is urgent", root=tmp_path, console=console, yolo=True)
    assert "HUMAN-CENTERED PRESENCE" in interactive.calls[0]["system"]
    assert "CURRENT-TURN DELIVERY CUE" in interactive.calls[0]["system"]

    background = FakeProvider(responses=[LLMResponse(text="done", stop_reason="end_turn")])
    with patch("ronin_cli.code_mode.build_provider", return_value=background):
        run_code_agent(config, "I am stuck and this is urgent", root=tmp_path, console=None, yolo=True)
    assert "HUMAN-CENTERED PRESENCE" not in background.calls[0]["system"]
