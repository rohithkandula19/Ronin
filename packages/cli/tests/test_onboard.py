"""Tests for first-run onboarding helpers."""
from __future__ import annotations

from ronin_cli.config import RoninConfig
from ronin_cli.onboard import PROVIDERS, build_config


def test_providers_catalogue_sane() -> None:
    assert len(PROVIDERS) >= 4
    keys = [p.key for p in PROVIDERS]
    assert "groq" in keys and "cerebras" in keys and "ollama" in keys
    # free providers come before the paid one
    assert keys.index("groq") < keys.index("anthropic")


def test_build_config_sets_provider_and_key() -> None:
    base = RoninConfig(provider="anthropic")
    choice = next(p for p in PROVIDERS if p.key == "groq")
    cfg = build_config(base, choice, "gsk_secret123")
    assert cfg.provider == "groq"
    assert cfg.resolved_model() == "llama-3.3-70b-versatile"
    assert cfg.key_for("groq") == "gsk_secret123"


def test_build_config_ollama_needs_no_key() -> None:
    base = RoninConfig(provider="anthropic")
    ollama = next(p for p in PROVIDERS if p.key == "ollama")
    cfg = build_config(base, ollama, None)
    assert cfg.provider == "ollama" and not ollama.needs_key


def test_build_config_strips_key_whitespace() -> None:
    base = RoninConfig()
    choice = next(p for p in PROVIDERS if p.key == "cerebras")
    cfg = build_config(base, choice, "  csk-abc  ")
    assert cfg.key_for("cerebras") == "csk-abc"


def test_every_provider_has_a_key_source() -> None:
    assert all(p.where for p in PROVIDERS)
    assert all(p.blurb for p in PROVIDERS)
