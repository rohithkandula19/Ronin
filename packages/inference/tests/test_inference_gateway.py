"""Inference gateway: kill switch, quotas, local-only, failover, idempotent ledger."""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_inference import (
    Gateway,
    InferenceRequest,
    KillSwitchError,
    MockProvider,
    Privacy,
    Provider,
    QuotaConfig,
    QuotaError,
    UsageLedger,
)

AT = "2026-07-22T10:00:00Z"


def _ledger(tmp_path: Path) -> UsageLedger:
    return UsageLedger(tmp_path / "usage.json")


def _req(**kw) -> InferenceRequest:
    base = dict(id="r1", user_id="u1", est_input_tokens=100, est_output_tokens=100)
    base.update(kw)
    return InferenceRequest.model_validate(base)


# ------------------------------------------------------------- kill switch

def test_kill_switch_blocks_all_inference(tmp_path: Path):
    gw = Gateway([MockProvider()], _ledger(tmp_path), kill_switch=True)
    with pytest.raises(KillSwitchError, match="billing remain available"):
        gw.run(_req(), "hello", at=AT)


# ---------------------------------------------------------------- local-only

def test_local_only_never_routes_to_remote(tmp_path: Path):
    remote = Provider(id="remote:big", local=False, capabilities={"text"},
                      price_in_per_mtok=3.0, price_out_per_mtok=15.0)
    local = MockProvider(id="mock:local")
    gw = Gateway([remote, local], _ledger(tmp_path))
    resp = gw.run(_req(privacy=Privacy.local_only), "hi", at=AT)
    assert resp.trace.selected == "mock:local"
    assert any("local_only" in reason for _, reason in resp.trace.rejected)


def test_local_only_with_no_local_provider_fails_closed(tmp_path: Path):
    remote = Provider(id="remote:big", local=False, capabilities={"text"},
                      price_in_per_mtok=3.0, price_out_per_mtok=15.0)
    gw = Gateway([remote], _ledger(tmp_path))
    with pytest.raises(Exception, match="no eligible provider"):
        gw.run(_req(privacy=Privacy.local_only), "hi", at=AT)


# ---------------------------------------------------------------- quotas

def test_request_cost_ceiling_refuses_before_calling(tmp_path: Path):
    pricey = Provider(id="remote:pricey", local=False, capabilities={"text"},
                      price_in_per_mtok=1000.0, price_out_per_mtok=1000.0)
    gw = Gateway([pricey], _ledger(tmp_path), QuotaConfig(max_request_cost=0.01))
    with pytest.raises(QuotaError, match="max_request_cost"):
        gw.run(_req(est_input_tokens=100_000, est_output_tokens=100_000), "hi", at=AT)


def test_user_daily_cost_hard_limit(tmp_path: Path):
    pricey = MockProvider(id="remote:p", local=False, capabilities={"text"},
                          price_in_per_mtok=100.0, price_out_per_mtok=100.0)
    ledger = _ledger(tmp_path)
    # price 100/Mtok → cost = in_tokens * 1e-4 ; limit $0.05
    gw = Gateway([pricey], ledger, QuotaConfig(user_daily_cost=0.05))
    # prompt length drives mock actual tokens (~len//4); 800 chars ≈ 200 input tokens
    # → ~$0.02 recorded; a second ~$0.04-estimate request then exceeds $0.05.
    gw.run(_req(id="r1", est_input_tokens=200, est_output_tokens=0), "a" * 800, at=AT)
    with pytest.raises(QuotaError, match="daily cost"):
        gw.run(_req(id="r2", est_input_tokens=400, est_output_tokens=0), "b", at=AT)


def test_max_tokens_guard_against_runaway(tmp_path: Path):
    gw = Gateway([MockProvider()], _ledger(tmp_path), QuotaConfig(max_tokens_per_request=100))
    with pytest.raises(QuotaError, match="max_tokens_per_request"):
        gw.run(_req(est_input_tokens=500, est_output_tokens=500), "hi", at=AT)


# --------------------------------------------------------------- failover

def test_failover_on_provider_failure_records_once(tmp_path: Path):
    bad = MockProvider(id="mock:bad", fail=True)
    good = MockProvider(id="mock:good")
    ledger = _ledger(tmp_path)
    gw = Gateway([bad, good], ledger)
    resp = gw.run(_req(), "hi", at=AT)
    assert resp.trace.selected == "mock:good"
    assert "mock:good" in resp.trace.failover
    assert len(ledger.records()) == 1  # one usage record despite two providers tried


# ---------------------------------------------------------------- ledger

def test_ledger_idempotent_on_retry(tmp_path: Path):
    ledger = _ledger(tmp_path)
    gw = Gateway([MockProvider()], ledger)
    gw.run(_req(id="same"), "hi", at=AT)
    gw.run(_req(id="same"), "hi again", at=AT)  # retry same request id
    assert len(ledger.records()) == 1  # billed once, not twice


def test_unknown_price_not_counted_as_free(tmp_path: Path):
    unknown = Provider(id="remote:unknown", local=False, capabilities={"text"},
                       price_in_per_mtok=None, price_out_per_mtok=None)

    def _complete(prompt):
        from ronin_inference import ProviderResult
        return ProviderResult(text="ok", input_tokens=10, output_tokens=10)

    unknown.complete = _complete  # type: ignore
    ledger = _ledger(tmp_path)
    gw = Gateway([unknown], ledger)
    resp = gw.run(_req(), "hi", at=AT)
    assert resp.usage.est_cost is None            # unknown, not 0.0
    assert ledger.unknown_cost_count(user_id="u1") == 1
    assert ledger.spent(user_id="u1") == 0.0      # known-cost sum, excludes unknowns


def test_routing_trace_records_candidates_and_reason(tmp_path: Path):
    local = MockProvider(id="mock:local")
    remote = Provider(id="remote:big", local=False, capabilities={"text"},
                      price_in_per_mtok=3.0, price_out_per_mtok=15.0)
    gw = Gateway([remote, local], _ledger(tmp_path))
    resp = gw.run(_req(), "hi", at=AT)
    assert set(resp.trace.candidates) == {"mock:local", "remote:big"}
    assert resp.trace.selected == "mock:local"  # local + free ranks first
    assert resp.trace.selected_reason
