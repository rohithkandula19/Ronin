"""Metrics: counters, cache hit rate, the snapshot dict, and prometheus exposition."""

from __future__ import annotations

import pytest

from ronin.obs import PROMETHEUS_METRICS, Metrics


def test_counters_accumulate() -> None:
    m = Metrics()
    m.record_turn()
    m.record_turn()
    m.record_tokens(100, 20)
    m.record_tokens(50, 5)
    m.add_cost(0.25)
    m.record_tool(ok=True)
    m.record_tool(ok=False)
    assert m.turns == 2
    assert m.input_tokens == 150
    assert m.output_tokens == 25
    assert m.cost_usd == 0.25
    assert m.tool_calls == 2
    assert m.tool_errors == 1


def test_cache_hit_rate() -> None:
    m = Metrics()
    assert m.cache_hit_rate() == 0.0  # no lookups → 0, not a ZeroDivisionError
    m.record_cache(hit=True)
    m.record_cache(hit=True)
    m.record_cache(hit=False)
    assert m.cache_hits == 2
    assert m.cache_misses == 1
    assert m.cache_hit_rate() == 2 / 3


def test_snapshot_carries_every_counter_and_the_rate() -> None:
    m = Metrics()
    m.record_turn()
    m.record_tool(ok=False)
    m.record_cache(hit=True)
    assert m.snapshot() == {
        "turns": 1,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "tool_calls": 1,
        "tool_errors": 1,
        "cache_hits": 1,
        "cache_misses": 0,
        "cache_hit_rate": 1.0,
    }


def test_render_prometheus_is_well_formed_for_every_metric() -> None:
    m = Metrics()
    m.record_turn()
    m.record_tokens(10, 3)
    m.add_cost(0.5)
    m.record_tool(ok=True)
    m.record_cache(hit=True)
    lines = m.render_prometheus().splitlines()
    for spec in PROMETHEUS_METRICS:
        assert f"# HELP {spec.name} {spec.help}" in lines
        assert f"# TYPE {spec.name} {spec.kind}" in lines
        value_lines = [line for line in lines if line.startswith(spec.name + " ")]
        assert len(value_lines) == 1


def test_render_prometheus_formats_ints_and_floats_cleanly() -> None:
    m = Metrics()
    m.record_turn()
    m.add_cost(0.30000000000000004)  # float noise the renderer must not print
    m.record_cache(hit=True)
    lines = m.render_prometheus().splitlines()
    assert "ronin_turns_total 1" in lines
    assert "ronin_cost_usd_total 0.3" in lines
    assert "# TYPE ronin_cache_hit_rate gauge" in lines
    assert "ronin_cache_hit_rate 1" in lines


def test_negative_inputs_are_rejected() -> None:
    m = Metrics()
    with pytest.raises(ValueError):
        m.record_tokens(-1, 0)
    with pytest.raises(ValueError):
        m.add_cost(-0.01)
    with pytest.raises(ValueError):
        m.record_turn(-1)
