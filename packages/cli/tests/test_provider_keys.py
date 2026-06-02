"""Tests for per-provider API key storage — the fix for keys getting clobbered
when you switch between OpenAI-compatible providers."""
from __future__ import annotations

from ronin_cli.config import RoninConfig


def test_set_key_for_keeps_each_provider_separate() -> None:
    """The bug: /login openai used to overwrite the cerebras key. Now each
    provider keeps its own."""
    cfg = RoninConfig(provider="cerebras")
    cfg.set_key_for("cerebras", "cba-key")
    cfg.set_key_for("openai", "oai-key")  # this used to clobber cerebras

    assert cfg.key_for("cerebras") == "cba-key"  # still there!
    assert cfg.key_for("openai") == "oai-key"


def test_key_for_defaults_to_active_provider() -> None:
    cfg = RoninConfig(provider="groq")
    cfg.set_key_for("groq", "g-key")
    assert cfg.key_for() == "g-key"


def test_key_for_falls_back_to_legacy_flat_field() -> None:
    """Old configs that only have the flat openai_api_key still resolve."""
    cfg = RoninConfig(provider="together", openai_api_key="legacy-key")
    assert cfg.key_for("together") == "legacy-key"


def test_key_for_anthropic_uses_anthropic_slot() -> None:
    cfg = RoninConfig(provider="anthropic", anthropic_api_key="sk-ant")
    assert cfg.key_for("anthropic") == "sk-ant"


def test_key_for_env_fallback(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    cfg = RoninConfig(provider="openai")
    assert cfg.key_for("openai") == "env-key"


def test_key_for_ollama_is_none() -> None:
    cfg = RoninConfig(provider="ollama")
    assert cfg.key_for("ollama") is None


def test_has_provider_auth_uses_per_provider_key() -> None:
    cfg = RoninConfig(provider="cerebras")
    assert cfg.has_provider_auth() is False
    cfg.set_key_for("cerebras", "cba-key")
    assert cfg.has_provider_auth() is True


def test_provider_keys_round_trip_through_save_load(tmp_path, monkeypatch) -> None:
    """Per-provider keys persist to disk and reload — so they survive restarts."""
    import ronin_cli.config as cfgmod

    monkeypatch.setattr(cfgmod, "PROJECT_DIR", tmp_path / ".csk")
    monkeypatch.setattr(cfgmod, "USER_DIR", tmp_path / "user")

    cfg = RoninConfig(provider="cerebras")
    cfg.set_key_for("cerebras", "cba-key")
    cfg.set_key_for("openai", "oai-key")
    cfgmod.save_config(cfg)

    reloaded = cfgmod.load_config()
    assert reloaded.key_for("cerebras") == "cba-key"
    assert reloaded.key_for("openai") == "oai-key"
