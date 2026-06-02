from __future__ import annotations

import pytest

from ronin_agent_patterns import AnthropicProvider, OpenAICompatProvider
from ronin_cli.config import RoninConfig
from ronin_cli.runner import build_provider


def test_anthropic_provider_chosen() -> None:
    config = RoninConfig(provider="anthropic", anthropic_api_key="sk-ant-x")
    provider = build_provider(config)
    assert isinstance(provider, AnthropicProvider)
    assert provider.model.startswith("claude-")


def test_ollama_provider_uses_localhost() -> None:
    config = RoninConfig(provider="ollama")
    provider = build_provider(config)
    assert isinstance(provider, OpenAICompatProvider)
    assert "localhost" in provider.base_url
    assert provider.model == "llama3.1"


def test_openai_provider() -> None:
    config = RoninConfig(provider="openai", openai_api_key="sk-x")
    provider = build_provider(config)
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.base_url == "https://api.openai.com/v1"
    assert provider.api_key == "sk-x"


def test_groq_preset() -> None:
    config = RoninConfig(provider="groq", openai_api_key="gsk-x")
    provider = build_provider(config)
    assert "groq.com" in provider.base_url


def test_together_preset() -> None:
    config = RoninConfig(provider="together", openai_api_key="tok-x")
    provider = build_provider(config)
    assert "together.xyz" in provider.base_url


def test_custom_provider_uses_explicit_base_url() -> None:
    config = RoninConfig(
        provider="custom",
        openai_api_key="x",
        base_url="https://my-llm-host.example.com/v1",
        model="my-model",
    )
    provider = build_provider(config)
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.base_url == "https://my-llm-host.example.com/v1"
    assert provider.model == "my-model"


def test_explicit_model_overrides_preset() -> None:
    config = RoninConfig(provider="anthropic", model="claude-opus-4-7", anthropic_api_key="x")
    provider = build_provider(config)
    assert provider.model == "claude-opus-4-7"


def test_doctor_via_main(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """csk doctor should print the provider field."""
    from typer.testing import CliRunner
    from ronin_cli.main import app
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init", "--demo", "-y"])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "provider" in result.stdout.lower()
