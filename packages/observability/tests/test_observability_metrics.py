"""Metrics: counters, gauges, histogram quantiles, and the cardinality guard."""

from __future__ import annotations

import pytest

from ronin_observability import (
    CardinalityGuard,
    Histogram,
    MetricError,
    MetricsRegistry,
)


def test_counter_increments_with_labels():
    reg = MetricsRegistry()
    reg.inc_counter("requests", labels={"route": "/a"})
    reg.inc_counter("requests", 2, labels={"route": "/a"})
    reg.inc_counter("requests", labels={"route": "/b"})
    assert reg.counter_value("requests", labels={"route": "/a"}) == 3
    assert reg.counter_value("requests", labels={"route": "/b"}) == 1


def test_counter_label_order_independent():
    reg = MetricsRegistry()
    reg.inc_counter("c", labels={"a": "1", "b": "2"})
    assert reg.counter_value("c", labels={"b": "2", "a": "1"}) == 1


def test_counter_rejects_negative():
    reg = MetricsRegistry()
    with pytest.raises(MetricError):
        reg.inc_counter("c", -1)


def test_missing_counter_is_zero():
    reg = MetricsRegistry()
    assert reg.counter_value("nope") == 0.0


def test_gauge_set_and_get():
    reg = MetricsRegistry()
    reg.set_gauge("temp", 21.5)
    reg.set_gauge("temp", 19.0)
    assert reg.gauge_value("temp") == 19.0
    assert reg.gauge_value("absent") is None


def test_histogram_count_and_sum():
    reg = MetricsRegistry()
    for v in (1.0, 2.0, 3.0):
        reg.observe("lat", v)
    h = reg.histogram("lat")
    assert h is not None
    assert h.count() == 3
    assert h.sum() == 6.0


def test_histogram_quantiles_deterministic():
    h = Histogram()
    for v in range(1, 101):  # 1..100
        h.observe(float(v))
    # nearest-rank: rank = ceil(q*n)
    assert h.p50() == 50.0
    assert h.p95() == 95.0
    assert h.p99() == 99.0


def test_quantile_empty_is_none():
    assert Histogram().quantile(0.5) is None


def test_quantile_out_of_range_rejected():
    h = Histogram()
    h.observe(1.0)
    with pytest.raises(MetricError):
        h.quantile(1.5)


def test_cardinality_guard_rejects_too_many_labels():
    reg = MetricsRegistry(CardinalityGuard(max_labels_per_metric=2))
    with pytest.raises(MetricError):
        reg.inc_counter("c", labels={"a": "1", "b": "2", "c": "3"})


def test_cardinality_guard_rejects_long_label_value():
    reg = MetricsRegistry(CardinalityGuard(max_label_value_len=4))
    with pytest.raises(MetricError):
        reg.inc_counter("c", labels={"k": "toolong"})


def test_cardinality_guard_bounds_distinct_series():
    reg = MetricsRegistry(CardinalityGuard(max_series_per_metric=2))
    reg.inc_counter("c", labels={"i": "0"})
    reg.inc_counter("c", labels={"i": "1"})
    with pytest.raises(MetricError):
        reg.inc_counter("c", labels={"i": "2"})
    # existing series still writable
    reg.inc_counter("c", labels={"i": "0"})
    assert reg.counter_value("c", labels={"i": "0"}) == 2


def test_empty_metric_name_rejected():
    reg = MetricsRegistry()
    with pytest.raises(MetricError):
        reg.inc_counter("  ")
