"""Provider x capability x price, and the one invariant that spends the user's money.

Ronin is free-provider-first, so the routing layer has one rule it must never break
quietly: **do not silently swap a free model for a paid one, and never let an unknown
price masquerade as free.** The pieces to enforce it already exist but were never put
together — every :class:`~ronin.providers.router.ModelSpec` carries its
:class:`~ronin.providers.types.Capabilities` and its per-million prices, and
:class:`~ronin.providers.router.RouterConfig` knows each role's failover chain. This module
is the missing view over them:

* :func:`pricing_tier` resolves the ambiguity the ledger already warned about — a
  ``price`` of ``0.0`` is **FREE** only for a genuinely local/keyless endpoint; for a
  hosted model with no price in config it is **UNKNOWN**, not free. That distinction is the
  whole point: "we don't know what this costs" must not read as "this is free."
* :func:`capability_matrix` is the provider x capability table the coverage audit
  (``docs/design/provider-protocol-coverage.md``) asked for — every configured model, its
  tier, and what it can do — as data a report or a test can assert on.
* :func:`fallback_concerns` walks each role's failover chain and flags every step that
  escalates cost (free → unknown/paid, or unknown → paid). A concern is not an error: a
  user may *want* "when my free model is down, pay to finish." It exists so the swap is
  **surfaced** (``ronin doctor`` renders it) rather than discovered on a bill.

Pure and offline: everything is a function of the config, so it is tested against
hand-built specs with no provider, no network, and no request made.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import Any

from .router import ModelSpec, Role, RouterConfig

#: Provider names that run on the user's own hardware — free by construction, whatever
#: the price fields say. Kept beside the OpenAI-compatible local servers the wizard knows.
LOCAL_PROVIDERS: frozenset[str] = frozenset({"mlx", "local", "local_adapter", "ollama"})

#: Hostnames that mean "this machine". A base_url pointing at one is a local endpoint.
_LOCAL_HOSTS: tuple[str, ...] = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]")


class PricingTier(StrEnum):
    """What a model costs to call, honestly.

    ``UNKNOWN`` is deliberately distinct from ``FREE``: a hosted model with no price in
    config costs *something* we cannot name, and treating that as free is exactly the
    silent free→paid swap this module exists to prevent.
    """

    FREE = "free"
    UNKNOWN = "unknown"
    PAID = "paid"


#: Escalation order. A move to a higher rank spends money the previous step did not:
#: FREE → UNKNOWN (might cost) → PAID (will cost). Used to decide which fallback steps
#: are worth surfacing.
_TIER_RANK: Mapping[PricingTier, int] = {
    PricingTier.FREE: 0,
    PricingTier.UNKNOWN: 1,
    PricingTier.PAID: 2,
}


def is_local(spec: ModelSpec) -> bool:
    """True when ``spec`` runs locally / keylessly — genuinely free, not just unpriced.

    A model is local if its provider is a known local runtime, its ``base_url`` points at
    this machine, or it needs no API key (``api_key_env`` empty). Any of those means the
    ``0.0`` price is a real zero, not a missing one.
    """
    if spec.provider in LOCAL_PROVIDERS:
        return True
    if not spec.api_key_env:
        return True
    url = spec.base_url.lower()
    return any(host in url for host in _LOCAL_HOSTS)


def pricing_tier(spec: ModelSpec) -> PricingTier:
    """The honest cost class of a model.

    PAID when it has a price; else FREE when it is local/keyless; else UNKNOWN — a hosted
    endpoint whose price config did not state, which is not the same as free.
    """
    if spec.priced:
        return PricingTier.PAID
    if is_local(spec):
        return PricingTier.FREE
    return PricingTier.UNKNOWN


# --------------------------------------------------------------------------- #
# capability matrix
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ModelCapabilityRow:
    """One model's row in the matrix: who it is, what it costs, and what config claims.

    ``capability_overrides`` is what the *config* declares, not what the model can
    do: the full answer lives on the built client, which merges these over the
    adapter's own defaults, and building every configured model to fill in a table
    would load model weights to print a row. Empty means "the adapter decides" —
    reported as undeclared rather than guessed, the same rule the pricing tier
    follows.
    """

    name: str
    provider: str
    model: str
    tier: PricingTier
    capability_overrides: Mapping[str, Any]


def capability_matrix(config: RouterConfig) -> tuple[ModelCapabilityRow, ...]:
    """Every configured model as a row, sorted by name — the provider x capability table."""
    return tuple(
        ModelCapabilityRow(
            name=spec.name,
            provider=spec.provider,
            model=spec.model,
            tier=pricing_tier(spec),
            capability_overrides=spec.capability_overrides,
        )
        for spec in sorted(config.models.values(), key=lambda s: s.name)
    )


# --------------------------------------------------------------------------- #
# the free→paid guard
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FallbackConcern:
    """One failover step that would spend more than the step before it.

    Not a failure — a deliberate "pay to finish when the free model is down" is valid. It
    is surfaced so the swap is a choice, not a surprise on a bill.
    """

    role: Role
    from_model: str
    from_tier: PricingTier
    to_model: str
    to_tier: PricingTier

    @property
    def message(self) -> str:
        return (
            f"role {self.role.value!r} falls back from {self.from_model} "
            f"({self.from_tier.value}) to {self.to_model} ({self.to_tier.value}) — "
            "a cost escalation"
        )


def _escalates(from_tier: PricingTier, to_tier: PricingTier) -> bool:
    return _TIER_RANK[to_tier] > _TIER_RANK[from_tier]


def fallback_concerns(config: RouterConfig) -> tuple[FallbackConcern, ...]:
    """Every fallback step, across all roles, that escalates cost.

    Walks each role's ``chain_for`` in order and compares consecutive tiers; a step whose
    target is more expensive/uncertain than its predecessor (free→unknown, free→paid,
    unknown→paid) is a concern. Steps that stay level or get cheaper are silent.
    """
    concerns: list[FallbackConcern] = []
    for role in sorted(config.fallbacks, key=lambda r: r.value):
        chain = config.chain_for(role)
        for previous, nxt in pairwise(chain):
            prev_tier = pricing_tier(previous)
            next_tier = pricing_tier(nxt)
            if _escalates(prev_tier, next_tier):
                concerns.append(
                    FallbackConcern(
                        role=role,
                        from_model=previous.name,
                        from_tier=prev_tier,
                        to_model=nxt.name,
                        to_tier=next_tier,
                    )
                )
    return tuple(concerns)


__all__ = [
    "LOCAL_PROVIDERS",
    "FallbackConcern",
    "ModelCapabilityRow",
    "PricingTier",
    "capability_matrix",
    "fallback_concerns",
    "is_local",
    "pricing_tier",
]
