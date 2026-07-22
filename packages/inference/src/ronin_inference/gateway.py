"""The inference gateway: route → check limits → execute → record — in one place."""

from __future__ import annotations

from dataclasses import dataclass, field

from .ledger import UsageLedger, UsageRecord
from .providers import Provider, ProviderResult
from .quota import QuotaConfig
from .request import InferenceRequest, Privacy


class GatewayError(RuntimeError):
    pass


class KillSwitchError(GatewayError):
    """Inference is globally disabled. Account/data/billing remain accessible."""


class QuotaError(GatewayError):
    """A quota or cost ceiling would be exceeded. Fail closed before spending."""


@dataclass
class RoutingTrace:
    candidates: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (provider, reason)
    selected: str = ""
    selected_reason: str = ""
    est_cost: float | None = None
    failover: list[str] = field(default_factory=list)
    privacy: str = "standard"


@dataclass
class GatewayResponse:
    result: ProviderResult
    trace: RoutingTrace
    usage: UsageRecord


class Gateway:
    def __init__(
        self,
        providers: list[Provider],
        ledger: UsageLedger,
        quota: QuotaConfig | None = None,
        *,
        kill_switch: bool = False,
    ) -> None:
        self._providers = providers
        self._ledger = ledger
        self._quota = quota or QuotaConfig()
        self.kill_switch = kill_switch

    # ------------------------------------------------------------- routing
    def _rank(self, req: InferenceRequest, trace: RoutingTrace) -> list[Provider]:
        need = set(req.required_capabilities)
        ranked: list[Provider] = []
        for p in self._providers:
            trace.candidates.append(p.id)
            if req.privacy is Privacy.local_only and not p.local:
                trace.rejected.append((p.id, "local_only: remote provider forbidden"))
                continue
            if not need <= p.capabilities:
                trace.rejected.append((p.id, f"missing capabilities {sorted(need - p.capabilities)}"))
                continue
            if not p.healthy:
                trace.rejected.append((p.id, "unhealthy"))
                continue
            ranked.append(p)
        # cheapest-known first, then local, then declared order; unknown-price last
        def key(p: Provider):
            c = p.estimate_cost(req.est_input_tokens, req.est_output_tokens)
            return (0 if p.local else 1, 1 if c is None else 0, c if c is not None else 0.0)
        ranked.sort(key=key)
        return ranked

    # -------------------------------------------------------------- limits
    def _check_quota(self, req: InferenceRequest, provider: Provider, at: str) -> float | None:
        est = provider.estimate_cost(req.est_input_tokens, req.est_output_tokens)
        q = self._quota
        tok = req.est_input_tokens + req.est_output_tokens
        if q.max_tokens_per_request is not None and tok > q.max_tokens_per_request:
            raise QuotaError(f"request tokens {tok} exceed max_tokens_per_request {q.max_tokens_per_request}")
        # cost ceilings only apply when the cost is known
        if est is not None:
            if q.max_request_cost is not None and est > q.max_request_cost:
                raise QuotaError(f"request est ${est:.4f} exceeds max_request_cost ${q.max_request_cost}")
            day = at[:10]
            if q.user_daily_cost is not None:
                spent = self._ledger.spent(user_id=req.user_id, since=day)
                if spent + est > q.user_daily_cost:
                    raise QuotaError(
                        f"user daily cost ${spent + est:.4f} would exceed ${q.user_daily_cost}")
            if q.org_monthly_cost is not None and req.org_id:
                spent = self._ledger.spent(org_id=req.org_id, since=at[:7])
                if spent + est > q.org_monthly_cost:
                    raise QuotaError(
                        f"org monthly cost ${spent + est:.4f} would exceed ${q.org_monthly_cost}")
            if q.provider_daily_cost is not None:
                spent = self._ledger.spent(provider=provider.id, since=day)
                if spent + est > q.provider_daily_cost:
                    raise QuotaError(
                        f"provider {provider.id} daily cost would exceed ${q.provider_daily_cost}")
        return est

    # ------------------------------------------------------------- execute
    def run(self, req: InferenceRequest, prompt: str, *, at: str) -> GatewayResponse:
        if self.kill_switch:
            raise KillSwitchError(
                "inference is globally disabled (kill switch on); account, data "
                "export, and billing remain available"
            )
        trace = RoutingTrace(privacy=req.privacy.value)
        ranked = self._rank(req, trace)
        if not ranked:
            raise GatewayError("no eligible provider for this request")

        last_err: Exception | None = None
        for i, provider in enumerate(ranked):
            try:
                est = self._check_quota(req, provider, at)
            except QuotaError:
                raise  # quota is a hard stop, not a failover reason
            if i > 0:
                trace.failover.append(provider.id)
            try:
                result = provider.complete(prompt)
            except Exception as exc:  # provider failure → try next eligible
                last_err = exc
                trace.rejected.append((provider.id, f"runtime failure: {exc}"))
                continue
            trace.selected = provider.id
            trace.selected_reason = "cheapest eligible" if i == 0 else "failover after upstream failure"
            trace.est_cost = est
            actual = provider.estimate_cost(result.input_tokens, result.output_tokens)
            usage = UsageRecord(
                request_id=req.id, user_id=req.user_id, org_id=req.org_id,
                provider=provider.id, industry=req.industry,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                cached_tokens=result.cached_tokens, est_cost=actual, at=at,
            )
            self._ledger.record(usage)  # idempotent on request id
            return GatewayResponse(result=result, trace=trace, usage=usage)

        raise GatewayError(f"all eligible providers failed; last error: {last_err}")
