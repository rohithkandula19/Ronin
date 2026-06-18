"""Tests for failover spec-building + fast-fail retry wiring."""
from __future__ import annotations

from ronin_cli.config import RoninConfig
from ronin_cli.config_cmd import failover_specs
from ronin_cli.runner import build_provider, default_failover_specs


def test_default_failover_specs_cerebras_siblings() -> None:
    oss = RoninConfig(provider="cerebras", model="gpt-oss-120b")
    assert default_failover_specs(oss) == [{"provider": "cerebras", "model": "zai-glm-4.7"}]
    glm = RoninConfig(provider="cerebras", model="zai-glm-4.7")
    assert default_failover_specs(glm) == [{"provider": "cerebras", "model": "gpt-oss-120b"}]


def test_default_failover_specs_none_for_other_providers() -> None:
    assert default_failover_specs(RoninConfig(provider="groq")) == []
    assert default_failover_specs(RoninConfig(provider="anthropic")) == []
    # an unknown cerebras model has no sibling -> no automatic fallback
    assert default_failover_specs(RoninConfig(provider="cerebras", model="weird-x")) == []


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
    # groq has no automatic same-key sibling, so with no failover it stays plain.
    cfg = RoninConfig(provider="groq", provider_keys={"groq": "x"})
    prov = build_provider(cfg)
    assert not hasattr(prov, "providers")   # a plain provider, not a FailoverProvider


def test_cerebras_default_failover_is_automatic_same_key() -> None:
    """With provider=cerebras and the default model, gpt-oss-120b's sibling
    (zai-glm-4.7) is wired as an automatic same-key fallback: no other provider
    and no extra key needed."""
    cfg = RoninConfig(provider="cerebras", provider_keys={"cerebras": "x"})
    prov = build_provider(cfg)
    assert hasattr(prov, "providers") and len(prov.providers) == 2
    # both links are Cerebras on the SAME key, just different models
    assert prov.providers[0].model == "gpt-oss-120b"
    assert prov.providers[1].model == "zai-glm-4.7"
    assert prov.providers[0].api_key == "x"
    assert prov.providers[1].api_key == "x"
    assert prov.labels[0] == "cerebras:gpt-oss-120b"
    assert prov.labels[1] == "cerebras:zai-glm-4.7"
    # the primary fails FAST so a 429 hops to the sibling instead of waiting ~60s
    assert prov.providers[0].max_retries == 1


def test_cerebras_glm_default_falls_back_to_oss() -> None:
    """The reverse direction: zai-glm-4.7 falls back to gpt-oss-120b."""
    cfg = RoninConfig(provider="cerebras", model="zai-glm-4.7",
                      provider_keys={"cerebras": "x"})
    prov = build_provider(cfg)
    assert hasattr(prov, "providers") and len(prov.providers) == 2
    assert prov.providers[0].model == "zai-glm-4.7"
    assert prov.providers[1].model == "gpt-oss-120b"


def test_explicit_failover_overrides_cerebras_default() -> None:
    """An explicit chain wins over the automatic same-key sibling."""
    cfg = RoninConfig(provider="cerebras", model="zai-glm-4.7",
                      provider_keys={"cerebras": "x", "groq": "y"},
                      failover=[{"provider": "groq"}])
    prov = build_provider(cfg)
    assert len(prov.providers) == 2
    assert prov.labels[1].startswith("groq:")


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
