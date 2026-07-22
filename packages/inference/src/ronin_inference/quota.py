"""Multi-level spending/token quotas. All optional; None = no limit at that level."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class QuotaScope(str, Enum):
    request = "request"
    user_daily = "user_daily"
    org_monthly = "org_monthly"
    provider_daily = "provider_daily"
    platform_monthly = "platform_monthly"


class QuotaConfig(BaseModel):
    """Cost ceilings (USD) per scope. None means unlimited at that scope."""

    model_config = ConfigDict(extra="forbid")

    max_request_cost: float | None = None
    user_daily_cost: float | None = None
    org_monthly_cost: float | None = None
    provider_daily_cost: float | None = None
    platform_monthly_cost: float | None = None
    # runaway-agent guards
    max_tokens_per_request: int | None = 200_000
