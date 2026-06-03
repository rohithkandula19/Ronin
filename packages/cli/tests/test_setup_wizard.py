"""Tests for the setup wizard's routing suggester."""
from __future__ import annotations

from ronin_cli.config import RoninConfig
from ronin_cli.setup_wizard import configured_providers, suggest_routing


def test_configured_includes_current_and_keyed() -> None:
    cfg = RoninConfig(provider="anthropic", provider_keys={"cerebras": "k", "groq": "k"})
    assert configured_providers(cfg) == {"anthropic", "cerebras", "groq"}


def test_suggest_free_fast_strong_current() -> None:
    cfg = RoninConfig(provider="anthropic", provider_keys={"cerebras": "k"})
    fast, strong = suggest_routing(cfg)
    assert fast.startswith("cerebras:") and strong.startswith("anthropic:")


def test_suggest_prefers_strong_among_configured() -> None:
    # current provider is a free one → strong should come from STRONG_PREFERENCE
    cfg = RoninConfig(provider="groq", provider_keys={"openai": "k"})
    fast, strong = suggest_routing(cfg)
    assert fast.startswith("groq:") and strong.startswith("openai:")


def test_none_when_only_one_provider() -> None:
    cfg = RoninConfig(provider="anthropic")   # nothing free configured
    assert suggest_routing(cfg) is None


def test_none_when_fast_equals_strong() -> None:
    # only a strong provider, no free blade → no split
    cfg = RoninConfig(provider="openai", provider_keys={})
    assert suggest_routing(cfg) is None
