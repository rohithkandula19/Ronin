"""Plan tiers derived from monthly request volume.

`tier_for` sits on the metering hot path - it is called once per request while
rating usage - hence the cache. It is also registered in RESOLVERS so the admin
console can list every pricing rule it knows about by name without importing
each module by hand.
"""

from __future__ import annotations

import functools
from collections.abc import Callable

RESOLVERS: dict[str, Callable[..., str]] = {}


def resolver(name: str) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Register a pricing resolver under `name` for the admin console."""

    def register(func: Callable[..., str]) -> Callable[..., str]:
        if name in RESOLVERS:
            raise RuntimeError(f"resolver {name!r} is already registered")
        RESOLVERS[name] = func
        return func

    return register


@resolver("tier")
@functools.lru_cache(maxsize=1024)
def tier_for(monthly_requests: int) -> str:
    """Return the plan tier an account making `monthly_requests` calls belongs to."""
    if monthly_requests < 0:
        raise ValueError(f"negative request count: {monthly_requests!r}")
    if monthly_requests < 1_000:
        return "free"
    if monthly_requests < 50_000:
        return "starter"
    if monthly_requests < 500_000:
        return "pro"
    return "scale"


@resolver("overage")
def overage_cents(monthly_requests: int) -> str:
    """Return the per-1k-request overage price, in cents, as a decimal string."""
    tier = tier_for(monthly_requests)
    return {"free": "0", "starter": "40", "pro": "25", "scale": "12"}.get(tier, "40")
