"""Tests for the Self-tuning Router — outcome stats + escalation."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.config import RoninConfig
from ronin_cli.router_stats import (
    ESCALATE_BELOW,
    MIN_SAMPLES,
    RouterStats,
    load_stats,
    save_stats,
)
from ronin_cli.routing import route_turn_config


def test_record_and_rate() -> None:
    s = RouterStats()
    # too few samples → no rate yet
    s.record("simple", "cerebras", True)
    assert s.rate("simple", "cerebras") is None
    # enough samples → a real rate
    for ok in (True, False, True, True):
        s.record("simple", "cerebras", ok)
    assert s.rate("simple", "cerebras") == 4 / 5


def test_should_escalate_only_when_unreliable() -> None:
    s = RouterStats()
    for ok in (False, False, False, True):   # 1/4 = 0.25 < threshold
        s.record("simple", "groq", ok)
    assert s.should_escalate("simple", "groq")
    # a reliable blade is never escalated
    s2 = RouterStats()
    for _ in range(MIN_SAMPLES):
        s2.record("simple", "cerebras", True)
    assert not s2.should_escalate("simple", "cerebras")


def test_round_trip_persistence(tmp_path: Path) -> None:
    s = RouterStats()
    s.record("complex", "anthropic", True)
    s.record("complex", "anthropic", False)
    save_stats(tmp_path, s)
    assert (tmp_path / ".ronin" / "router_stats.json").is_file()
    loaded = load_stats(tmp_path)
    assert loaded.data["complex"]["anthropic"] == [1, 2]


def test_load_missing_is_empty(tmp_path: Path) -> None:
    assert load_stats(tmp_path).data == {}


def test_route_escalates_unreliable_cheap_blade() -> None:
    cfg = RoninConfig(provider="anthropic",
                      route_fast="cerebras:gpt-oss-120b",
                      route_strong="anthropic:claude-sonnet-4-6")
    stats = RouterStats()
    for ok in (False, False, False, False):   # cerebras keeps failing simple turns
        stats.record("simple", "cerebras", ok)
    turn_cfg, decision = route_turn_config(cfg, "hey", stats=stats)
    # escalated off the cheap blade to the strong one
    assert decision.escalated and turn_cfg.provider == "anthropic"


def test_route_keeps_reliable_cheap_blade() -> None:
    cfg = RoninConfig(provider="anthropic",
                      route_fast="cerebras:gpt-oss-120b",
                      route_strong="anthropic:claude-sonnet-4-6")
    stats = RouterStats()
    for _ in range(MIN_SAMPLES):
        stats.record("simple", "cerebras", True)
    turn_cfg, decision = route_turn_config(cfg, "hey", stats=stats)
    assert not decision.escalated and turn_cfg.provider == "cerebras"
