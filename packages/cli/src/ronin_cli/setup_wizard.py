"""Setup wizard helpers — get a new user routing in one command.

The Cost Router, Scout→Strike, and self-tuning all need ``route_fast`` /
``route_strong`` set, which most people won't configure by hand. This suggests a
sensible split from the providers you've already got keys for: a free/cheap blade
for simple turns, a strong blade for hard ones. The suggestion logic is pure and
unit-tested; the ``ronin setup`` command wraps it with prompts.
"""
from __future__ import annotations

from .config import PROVIDER_PRESETS, RoninConfig

# Free/cheap blades, best-first, for the route_fast slot.
FAST_PREFERENCE = ("cerebras", "groq", "gemini", "openrouter", "ollama")
# Strong blades, best-first, for the route_strong slot.
STRONG_PREFERENCE = ("anthropic", "openai")


def _spec(provider: str) -> str:
    """A "provider:model" route target using the provider's default model."""
    model = PROVIDER_PRESETS.get(provider, {}).get("model") or ""
    return f"{provider}:{model}" if model else provider


def configured_providers(config: RoninConfig) -> set[str]:
    """Providers we can actually use: those with a stored key, plus the current
    one (its key may come from an env var)."""
    provs = set(config.provider_keys.keys())
    provs.add(config.provider)
    return {p for p in provs if p}


def suggest_routing(config: RoninConfig) -> tuple[str, str] | None:
    """Suggest ``(route_fast, route_strong)`` from configured providers, or None
    when there's no sensible free+strong split available."""
    provs = configured_providers(config)
    fast = next((p for p in FAST_PREFERENCE if p in provs), None)
    if config.provider in STRONG_PREFERENCE:
        strong = config.provider
    else:
        strong = next((p for p in STRONG_PREFERENCE if p in provs), config.provider)
    if fast is None or _spec(fast) == _spec(strong):
        return None
    return _spec(fast), _spec(strong)
