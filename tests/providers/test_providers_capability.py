"""Pricing tiers, the capability matrix, and the free→paid fallback guard.

All pure: a ModelSpec is data, so the honest-cost classification, the matrix, and the
cost-escalation detector are tested against hand-built specs with no client, no network,
and no request. The one subtlety worth stating is why ``0.0`` is not always "free": a
local/keyless endpoint priced at zero is genuinely free, but a *hosted* model with no
price in config is UNKNOWN — and conflating the two is the silent free→paid swap the guard
exists to prevent.
"""

from __future__ import annotations

from ronin.providers.capability import (
    PricingTier,
    capability_matrix,
    fallback_concerns,
    is_local,
    pricing_tier,
)
from ronin.providers.router import ModelSpec, Role, RouterConfig


def _spec(name: str, **kw: object) -> ModelSpec:
    base: dict[str, object] = {"provider": "anthropic", "model": "m"}
    base.update(kw)
    return ModelSpec(name=name, **base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# pricing tier
# --------------------------------------------------------------------------- #


def test_priced_model_is_paid() -> None:
    spec = _spec("claude", api_key_env="ANTHROPIC_API_KEY", price_in=3.0, price_out=15.0)
    assert pricing_tier(spec) is PricingTier.PAID


def test_local_provider_is_free_even_at_zero_price() -> None:
    assert pricing_tier(_spec("q", provider="mlx")) is PricingTier.FREE


def test_keyless_endpoint_is_free() -> None:
    # No api_key_env → nothing to authenticate → a local/keyless server, genuinely free.
    assert pricing_tier(_spec("ollama", provider="openai-compatible", api_key_env="")) is (
        PricingTier.FREE
    )


def test_localhost_base_url_is_free() -> None:
    spec = _spec("local", api_key_env="X", base_url="http://127.0.0.1:11434/v1")
    assert is_local(spec) and pricing_tier(spec) is PricingTier.FREE


def test_hosted_without_price_is_unknown_not_free() -> None:
    # A hosted, key-requiring model with no price is the trap: unknown, never "free".
    spec = _spec(
        "groqx", provider="groq", api_key_env="GROQ_API_KEY", base_url="https://api.groq.com"
    )
    assert not is_local(spec)
    assert pricing_tier(spec) is PricingTier.UNKNOWN


# --------------------------------------------------------------------------- #
# capability matrix
# --------------------------------------------------------------------------- #


def test_matrix_is_sorted_and_carries_tier_and_declared_capabilities() -> None:
    """The row reports what config *declared*, which is not what the model can do.

    The full picture lives on the built client, which merges these over the
    adapter's own defaults — and building every configured model to print a table
    would load model weights for a row of text. So the row is honest about being
    partial: an empty mapping means "the adapter decides", the same way an absent
    price means UNKNOWN rather than free.
    """
    overrides = {"native_tools": False, "max_context": 32_768}
    free = _spec("aaa-local", provider="mlx", capability_overrides=overrides)
    paid = _spec("zzz-claude", api_key_env="ANTHROPIC_API_KEY", price_in=3.0, price_out=15.0)
    config = RouterConfig(
        roles={Role.MAIN: "aaa-local"}, models={"aaa-local": free, "zzz-claude": paid}
    )
    rows = capability_matrix(config)
    assert [r.name for r in rows] == ["aaa-local", "zzz-claude"]  # sorted by name
    assert rows[0].tier is PricingTier.FREE and rows[0].capability_overrides == overrides
    assert rows[1].tier is PricingTier.PAID and rows[1].capability_overrides == {}


# --------------------------------------------------------------------------- #
# fallback cost guard
# --------------------------------------------------------------------------- #


def _config(chain: list[ModelSpec]) -> RouterConfig:
    models = {s.name: s for s in chain}
    primary, *rest = chain
    return RouterConfig(
        roles={Role.MAIN: primary.name},
        models=models,
        fallbacks={Role.MAIN: tuple(s.name for s in rest)} if rest else {},
    )


def test_free_to_paid_fallback_is_flagged() -> None:
    free = _spec("local", provider="mlx")
    paid = _spec("claude", api_key_env="K", price_in=3.0, price_out=15.0)
    concerns = fallback_concerns(_config([free, paid]))
    assert len(concerns) == 1
    assert concerns[0].from_tier is PricingTier.FREE and concerns[0].to_tier is PricingTier.PAID
    assert "cost escalation" in concerns[0].message


def test_free_to_unknown_and_unknown_to_paid_are_flagged() -> None:
    free = _spec("local", provider="mlx")
    unknown = _spec("groqx", provider="groq", api_key_env="G", base_url="https://api.groq.com")
    paid = _spec("claude", api_key_env="K", price_in=3.0, price_out=15.0)
    # free -> unknown -> paid: two escalating steps.
    concerns = fallback_concerns(_config([free, unknown, paid]))
    assert len(concerns) == 2


def test_de_escalation_and_same_tier_are_silent() -> None:
    paid = _spec("claude", api_key_env="K", price_in=3.0, price_out=15.0)
    free = _spec("local", provider="mlx")
    free2 = _spec("local2", provider="mlx")
    assert fallback_concerns(_config([paid, free])) == ()  # paid -> free is fine
    assert fallback_concerns(_config([free, free2])) == ()  # free -> free is fine


def test_no_fallbacks_means_no_concerns() -> None:
    assert fallback_concerns(_config([_spec("local", provider="mlx")])) == ()
