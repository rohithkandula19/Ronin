"""Pure (no-network) sanity tests for PROVIDER_PRESETS and config resolution.

The mission is "free for everyone", so these guard the free-provider path: every
preset must carry a usable default model + base_url, and a bare
``RoninConfig(provider=X)`` must resolve to a sane model/base_url for each free
provider — so a brand-new user with just a free key never hits an empty/None
model or a missing endpoint on their first run.
"""
from __future__ import annotations

import pytest

from ronin_cli.config import PROVIDER_PRESETS, RoninConfig

# Providers that are free / no-credit-card (ollama is local). These are the ones
# a frictionless first run depends on most.
FREE_PROVIDERS = ["openrouter", "groq", "gemini", "cerebras", "ollama"]


def test_every_preset_has_a_nonempty_model() -> None:
    """No preset may ship an empty/missing default model — that would 400/404 on
    the very first call before the user could do anything."""
    for name, preset in PROVIDER_PRESETS.items():
        model = preset.get("model")
        assert isinstance(model, str) and model.strip(), f"{name} preset has no model"


def test_every_preset_has_a_base_url_except_anthropic() -> None:
    """Every OpenAI-compatible preset needs a base_url to reach the endpoint.
    Anthropic is the one exception: it uses the native SDK, not a base_url."""
    for name, preset in PROVIDER_PRESETS.items():
        base = preset.get("base_url", "")
        if name == "anthropic":
            assert base == "", "anthropic uses the native SDK and should have no base_url"
        else:
            assert isinstance(base, str) and base.strip(), f"{name} preset has no base_url"
            assert base.startswith("http"), f"{name} base_url is not a URL: {base!r}"


@pytest.mark.parametrize("provider", FREE_PROVIDERS)
def test_free_provider_resolves_a_sane_model(provider: str) -> None:
    """A bare config for each free provider resolves to its preset model (never
    None / empty), matching the preset table."""
    cfg = RoninConfig(provider=provider)
    model = cfg.resolved_model()
    assert isinstance(model, str) and model.strip()
    assert model == PROVIDER_PRESETS[provider]["model"]


@pytest.mark.parametrize("provider", FREE_PROVIDERS)
def test_free_provider_resolves_a_sane_base_url(provider: str) -> None:
    """Each free provider is OpenAI-compatible, so a bare config must resolve to
    an http(s) base_url (none of these are the native-SDK anthropic path)."""
    cfg = RoninConfig(provider=provider)
    base = cfg.resolved_base_url()
    assert base is not None and base.startswith("http")
    assert base == PROVIDER_PRESETS[provider]["base_url"]


def test_openrouter_default_is_a_free_model() -> None:
    """OpenRouter bills per-model; the default must be a ``:free`` id so a new
    user with a free key isn't charged on their first run."""
    assert RoninConfig(provider="openrouter").resolved_model().endswith(":free")


def test_explicit_model_overrides_free_preset() -> None:
    """An explicit --model always wins over the preset default."""
    cfg = RoninConfig(provider="groq", model="some/custom-model")
    assert cfg.resolved_model() == "some/custom-model"


def test_explicit_base_url_overrides_free_preset() -> None:
    cfg = RoninConfig(provider="cerebras", base_url="https://example.test/v1")
    assert cfg.resolved_base_url() == "https://example.test/v1"


def test_ollama_is_local_and_needs_no_key() -> None:
    """Ollama is the local free path: localhost base_url, no auth required."""
    cfg = RoninConfig(provider="ollama")
    assert "localhost" in (cfg.resolved_base_url() or "")
    assert cfg.has_provider_auth() is True  # local server, no key needed


def test_unknown_provider_falls_back_without_crashing() -> None:
    """An unknown provider must not crash model resolution (falls back to a
    sane default rather than KeyError)."""
    cfg = RoninConfig(provider="totally-made-up")
    assert isinstance(cfg.resolved_model(), str) and cfg.resolved_model().strip()
    assert cfg.resolved_base_url() is None  # no preset → no base_url
