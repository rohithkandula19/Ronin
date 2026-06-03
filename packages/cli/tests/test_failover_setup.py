"""Tests for failover spec-building + fast-fail retry wiring."""
from __future__ import annotations

from ronin_cli.config import RoninConfig
from ronin_cli.config_cmd import failover_specs
from ronin_cli.runner import build_provider


def test_failover_specs_simple() -> None:
    assert failover_specs("groq,gemini") == [{"provider": "groq"}, {"provider": "gemini"}]


def test_failover_specs_with_models() -> None:
    specs = failover_specs("groq:llama-3.3-70b, openrouter:qwen/q:free")
    assert specs[0] == {"provider": "groq", "model": "llama-3.3-70b"}
    assert specs[1] == {"provider": "openrouter", "model": "qwen/q:free"}


def test_failover_specs_empty() -> None:
    assert failover_specs("") == []
    assert failover_specs(" , ,") == []


def test_build_provider_no_failover_is_plain() -> None:
    cfg = RoninConfig(provider="cerebras", provider_keys={"cerebras": "x"})
    prov = build_provider(cfg)
    assert not hasattr(prov, "providers")   # a plain provider, not a FailoverProvider


def test_failover_chain_fails_fast_on_non_last() -> None:
    cfg = RoninConfig(provider="cerebras",
                      provider_keys={"cerebras": "x", "groq": "y", "gemini": "z"},
                      failover=[{"provider": "groq"}, {"provider": "gemini"}])
    prov = build_provider(cfg)
    assert hasattr(prov, "providers") and len(prov.providers) == 3
    # primary + middle fail fast (1 retry); the last keeps full retries
    assert prov.providers[0].max_retries == 1     # cerebras
    assert prov.providers[1].max_retries == 1     # groq
    assert prov.providers[2].max_retries > 1      # gemini (last → patient)
    # human-readable chain labels
    assert "cerebras" in prov.labels[0] and "gemini" in prov.labels[2]
