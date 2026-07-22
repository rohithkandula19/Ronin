"""Ronin AI OS inference gateway — the single choke point for model spend.

Every model request (web, API, worker) is meant to pass through the gateway so
routing, cost limits, quotas, and the kill switch are enforced in ONE place —
not re-implemented per surface. Design guarantees, each tested:

- **No uncontrolled spending:** a global kill switch and multi-level quotas
  (request / user-daily / org-monthly / provider-daily) stop work at hard
  limits; a request whose estimated cost exceeds the remaining budget is
  refused BEFORE any provider call.
- **Local-only is honored:** a request marked local_only can never be routed to
  a remote provider, even on failover.
- **No double billing on retry:** the usage ledger is append-only and keyed by
  request id, so a retried request records usage once.
- **Honest cost:** unknown provider prices are never rendered as free.
"""

from .gateway import (
    Gateway,
    GatewayError,
    KillSwitchError,
    QuotaError,
    RoutingTrace,
)
from .ledger import UsageLedger, UsageRecord
from .quota import QuotaConfig, QuotaScope
from .request import InferenceRequest, Privacy
from .providers import MockProvider, Provider, ProviderResult

__all__ = [
    "Gateway",
    "GatewayError",
    "InferenceRequest",
    "KillSwitchError",
    "MockProvider",
    "Privacy",
    "Provider",
    "ProviderResult",
    "QuotaConfig",
    "QuotaError",
    "QuotaScope",
    "RoutingTrace",
    "UsageLedger",
    "UsageRecord",
]
