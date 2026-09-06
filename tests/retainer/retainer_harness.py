"""Builders for retainer tests.

Named for the subsystem because the test trees are deliberately not packages, so
every module shares one flat namespace and a second ``harness.py`` would collide.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ronin.retainer.model import (
    Budgets,
    Capability,
    Channel,
    Deployment,
    Post,
    Retainer,
    StandingOrders,
)
from ronin.safety.policy import Decision, Rule

WORKSPACE = Path("/srv/posts/ronin")


def post(**kwargs: Any) -> Post:
    return Post(**{"repo": "rohithkandula19/Ronin", "workspace": WORKSPACE, **kwargs})


def laptop(**kwargs: Any) -> Deployment:
    """A local deployment holding everything, including the browser."""
    return Deployment(**{"name": "laptop", "capabilities": frozenset(Capability), **kwargs})


def server(**kwargs: Any) -> Deployment:
    """A hosted deployment. It cannot hold the browser capability at all."""
    return Deployment(
        **{
            "name": "fly",
            "hosted": True,
            "capabilities": frozenset({Capability.SHELL, Capability.NETWORK}),
            **kwargs,
        }
    )


def orders(**kwargs: Any) -> StandingOrders:
    return StandingOrders(
        **{
            "brief": "keep CI green",
            "tools": frozenset({"read", "bash"}),
            "default": Decision.DENY,
            "budgets": Budgets(),
            **kwargs,
        }
    )


def retainer(**kwargs: Any) -> Retainer:
    return Retainer(
        **{
            "id": "sentry",
            "name": "Sentry",
            "post": post(),
            "orders": orders(),
            "channels": frozenset({Channel.GITHUB}),
            **kwargs,
        }
    )


def rule(tool: str, decision: Decision, **kwargs: Any) -> Rule:
    """One order, built through the same parser a config file goes through."""
    from ronin.retainer.orders import parse_grants

    entry: dict[str, Any] = {"tool": tool, "decision": decision.value, **kwargs}
    return parse_grants([entry])[0]
