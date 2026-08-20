"""Contracts for provider-aware context-window budgets."""
from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from ronin_cli.code_mode import handle_slash_command
from ronin_cli.config import RoninConfig, load_config
from ronin_cli.context_policy import parse_context_window, resolve_context_policy
from ronin_cli.main import app


def test_context_policy_uses_model_catalog_and_reserves_output() -> None:
    policy = resolve_context_policy(RoninConfig(provider="anthropic", model="claude-sonnet-4-6"))
    assert policy.window_tokens == 200_000
    assert policy.input_budget_tokens < policy.window_tokens
    assert policy.output_reserve_tokens > 0
    assert policy.compaction_threshold_tokens < policy.input_budget_tokens
    assert policy.retrieval_budget_tokens == 8_000
    assert policy.source == "model catalog"


def test_context_policy_falls_back_safely_and_fast_mode_compacts_earlier() -> None:
    fallback = resolve_context_policy(RoninConfig(provider="custom", model="unknown-model"))
    fast = resolve_context_policy(RoninConfig(provider="cerebras", fast=True))
    assert fallback.window_tokens == 32_000
    assert fallback.retrieval_budget_tokens < 8_000
    assert fast.compaction_threshold_tokens <= 16_000


def test_context_policy_accepts_bounded_operator_override() -> None:
    policy = resolve_context_policy(RoninConfig(provider="ollama", context_window=64_000))
    assert policy.window_tokens == 64_000 and policy.source == "config override"
    assert parse_context_window("64k") == 64_000
    assert parse_context_window("1m") == 1_000_000
    with pytest.raises(ValueError, match="between"):
        parse_context_window("2048")
    with pytest.raises(ValueError, match="whole token"):
        parse_context_window("64.5k")


def test_context_slash_command_persists_override_and_shows_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, width=120)
    config = RoninConfig(provider="ollama")
    action = handle_slash_command(
        "/context 64k", console=console, root=tmp_path, config=config,
        undo_stack=[], transcript=["USER: inspect this"], message_history=[],
    )
    assert action == "handled" and config.context_window == 64_000
    assert load_config().context_window == 64_000
    output = stream.getvalue()
    assert "window 64,000" in output and "compact at" in output

    stream.truncate(0)
    stream.seek(0)
    handle_slash_command(
        "/context auto", console=console, root=tmp_path, config=config,
        undo_stack=[], transcript=[], message_history=[],
    )
    assert config.context_window is None and "context window auto" in stream.getvalue()


def test_code_agent_uses_the_resolved_window_for_compaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ronin_cli.code_mode as code_mode
    from ronin_agent_patterns import FakeProvider, LLMResponse

    captured: dict[str, object] = {}
    real_agent = code_mode.ReActAgent

    def spy(**kwargs):
        captured.update(kwargs)
        return real_agent(**kwargs)

    monkeypatch.setattr(code_mode, "ReActAgent", spy)
    monkeypatch.setattr(
        code_mode, "build_provider",
        lambda _config: FakeProvider(responses=[LLMResponse(text="done", stop_reason="end_turn")]),
    )
    config = RoninConfig(provider="ollama", context_window=64_000)
    code_mode.run_code_agent(config, "explain the project", root=tmp_path, console=None, yolo=True)
    assert captured["compact_after_tokens"] == resolve_context_policy(config).compaction_threshold_tokens


def test_code_cli_context_window_is_a_temporary_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(config, *_args, **_kwargs):
        captured["window"] = config.context_window
        return SimpleNamespace(blocked=False, streamed=False, output="done", iterations=1, usage={})

    with patch("ronin_cli.main.load_config", return_value=RoninConfig()), patch(
        "ronin_cli.agent_mode.has_real_key", return_value=True,
    ), patch("ronin_cli.code_mode.run_code_agent", side_effect=fake_run):
        result = CliRunner().invoke(app, ["code", "--context-window", "64000", "inspect the project"])

    assert result.exit_code == 0, result.stdout
    assert captured["window"] == 64_000
