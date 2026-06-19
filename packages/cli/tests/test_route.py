"""Tests for the Auto-Router — pure task classification + cost-aware blade pick.

Both functions under test are pure and deterministic: no network, no LLM, no
disk. ``route()`` itself (which runs the agent) is intentionally not exercised
here — it's the side-effecting orchestrator over these two scoring primitives.
"""
from __future__ import annotations

import pytest

from ronin_cli.route import (
    build_candidates,
    classify_task,
    pick_blade,
    reliability_bar,
    tier_for_class,
)
from ronin_cli.config import RoninConfig
from ronin_cli.router_stats import RouterStats


# ---------------------------------------------------------------------------
# classify_task
# ---------------------------------------------------------------------------

def test_classify_cheap_keywords() -> None:
    assert classify_task("rename a var") == "cheap"
    assert classify_task("fix this typo in the readme") == "cheap"
    assert classify_task("format the file") == "cheap"
    assert classify_task("reword this comment") == "cheap"


def test_classify_hard_keywords() -> None:
    assert classify_task("refactor the whole auth architecture") == "hard"
    assert classify_task("debug the failing integration test") == "hard"
    assert classify_task("migrate the schema to the new format") == "hard"
    assert classify_task("redesign the architecture of the scheduler") == "hard"


def test_hard_signal_beats_cheap_signal() -> None:
    # contains "rename" (cheap) AND "refactor"/"architecture" (hard) → hard wins
    assert classify_task("rename every call site while refactoring the auth architecture") == "hard"


def test_classify_standard_default() -> None:
    # a mid-length, signal-free ask is neither cheap nor hard
    assert classify_task("add a new endpoint that returns the list of active users") == "standard"


def test_classify_length_signals() -> None:
    # a very short, signal-free ask leans cheap
    assert classify_task("add a log") == "cheap"
    # a long dense ask is hard even without a keyword
    long = "please walk through " + ("the module and explain each function in detail " * 8)
    assert classify_task(long) == "hard"


def test_classify_code_block_is_hard() -> None:
    assert classify_task("what does this do?\n```python\nprint(1)\n```") == "hard"


def test_classify_empty_is_standard() -> None:
    assert classify_task("") == "standard"
    assert classify_task("   ") == "standard"


def test_class_to_tier_mapping() -> None:
    assert tier_for_class("cheap") == "simple"
    assert tier_for_class("standard") == "complex"
    assert tier_for_class("hard") == "complex"


def test_reliability_bar_rises_with_difficulty() -> None:
    assert reliability_bar("cheap") < reliability_bar("standard") < reliability_bar("hard")


# ---------------------------------------------------------------------------
# pick_blade
# ---------------------------------------------------------------------------

def _cand(provider: str, price: float, reliability: float | None = None,
          model: str | None = None) -> dict:
    c = {"provider": provider, "model": model or f"{provider}-model",
         "price_per_mtok": price}
    if reliability is not None:
        c["reliability"] = reliability
    return c


def test_pick_cheapest_above_the_bar() -> None:
    cands = [
        _cand("anthropic", price=9.0, reliability=0.99),
        _cand("cerebras", price=0.0, reliability=0.90),   # cheapest AND reliable
        _cand("openai", price=0.4, reliability=0.95),
    ]
    chosen = pick_blade("standard", cands)
    assert chosen["provider"] == "cerebras"
    assert chosen["reason"] == "cheapest above the bar"


def test_pick_skips_cheap_but_unreliable() -> None:
    # cerebras is free but flaky for this class; the next cheapest reliable wins
    cands = [
        _cand("cerebras", price=0.0, reliability=0.20),   # too unreliable
        _cand("openai", price=0.4, reliability=0.90),     # cheapest reliable
        _cand("anthropic", price=9.0, reliability=0.99),
    ]
    chosen = pick_blade("standard", cands)
    assert chosen["provider"] == "openai"
    assert chosen["reason"] == "cheapest above the bar"


def test_pick_falls_back_to_strongest_when_none_clear_bar() -> None:
    cands = [
        _cand("cerebras", price=0.0, reliability=0.30),
        _cand("groq", price=0.0, reliability=0.40),
        _cand("anthropic", price=9.0, reliability=0.55),  # strongest, still < hard bar
    ]
    chosen = pick_blade("hard", cands)
    assert chosen["provider"] == "anthropic"
    assert chosen["reason"] == "strongest (none cleared the bar)"


def test_pick_reads_reliability_from_stats() -> None:
    # no explicit reliability on candidates → pull learned rate from stats.
    stats = RouterStats()
    for ok in (True, True, True, True, True):              # cerebras reliable: 5/5 on simple
        stats.record("simple", "cerebras", ok)
    for ok in (False, False, False, True):                 # groq flaky: 1/4 on simple
        stats.record("simple", "groq", ok)
    cands = [_cand("cerebras", price=0.0), _cand("groq", price=0.0)]
    chosen = pick_blade("cheap", cands, stats=stats)        # cheap → tier "simple"
    assert chosen["provider"] == "cerebras"


def test_pick_unknown_reliability_uses_default() -> None:
    # a blade nobody has tried gets the neutral default — good enough for cheap.
    cands = [_cand("cerebras", price=0.0)]
    chosen = pick_blade("cheap", cands, stats=RouterStats())
    assert chosen["provider"] == "cerebras"
    assert chosen["eff_reliability"] == pytest.approx(0.7)


def test_pick_budget_drops_pricey_blades() -> None:
    cands = [
        _cand("anthropic", price=9.0, reliability=0.99),   # priced out by budget
        _cand("openai", price=0.4, reliability=0.95),
    ]
    chosen = pick_blade("standard", cands, budget=1.0)
    assert chosen["provider"] == "openai"


def test_pick_budget_too_low_keeps_cheapest() -> None:
    # budget below every blade → never return nothing; keep the single cheapest.
    cands = [
        _cand("openai", price=0.4, reliability=0.95),
        _cand("anthropic", price=9.0, reliability=0.99),
    ]
    chosen = pick_blade("standard", cands, budget=0.0)
    assert chosen["provider"] == "openai"
    assert chosen["reason"] == "cheapest under budget"


def test_pick_single_candidate() -> None:
    chosen = pick_blade("hard", [_cand("anthropic", price=9.0, reliability=0.1)])
    assert chosen["provider"] == "anthropic"
    assert chosen["reason"] == "only candidate"


def test_pick_no_candidates_raises() -> None:
    with pytest.raises(ValueError):
        pick_blade("standard", [])


def test_pick_is_deterministic_on_ties() -> None:
    # two equally cheap, equally reliable blades → stable choice by name.
    cands = [
        _cand("zeta", price=0.0, reliability=0.9),
        _cand("alpha", price=0.0, reliability=0.9),
    ]
    first = pick_blade("cheap", [dict(c) for c in cands])
    second = pick_blade("cheap", [dict(c) for c in cands])
    assert first["provider"] == second["provider"] == "alpha"


# ---------------------------------------------------------------------------
# build_candidates (pure given a config; prices via cost.py)
# ---------------------------------------------------------------------------

def test_build_candidates_from_route_targets() -> None:
    cfg = RoninConfig(provider="anthropic",
                      route_fast="cerebras:gpt-oss-120b",
                      route_strong="anthropic:claude-sonnet-4-6")
    cands = build_candidates(cfg)
    provs = {c["provider"] for c in cands}
    assert "cerebras" in provs and "anthropic" in provs
    # free blade is priced at 0, strong blade is priced > 0
    cere = next(c for c in cands if c["provider"] == "cerebras")
    anth = next(c for c in cands if c["provider"] == "anthropic")
    assert cere["price_per_mtok"] == 0.0
    assert anth["price_per_mtok"] > 0.0


def test_build_candidates_includes_failover_and_dedupes() -> None:
    cfg = RoninConfig(provider="anthropic",
                      failover=[{"provider": "gemini", "model": "gemini-flash-latest"},
                                {"provider": "anthropic"}])  # dup of active provider family
    cands = build_candidates(cfg)
    keys = [(c["provider"], c["model"]) for c in cands]
    assert ("gemini", "gemini-flash-latest") in keys
    # no duplicate (provider, model) pairs
    assert len(keys) == len(set(keys))
