"""Health: worst-wins aggregation and fail-closed behaviour."""

from __future__ import annotations

import pytest

from ronin_observability import (
    HealthRegistry,
    HealthResult,
    HealthStatus,
    aggregate,
)

NOW = "2026-07-22T00:00:00Z"


def _res(name: str, status: HealthStatus) -> HealthResult:
    return HealthResult(name=name, status=status)


def test_aggregate_worst_wins():
    results = (
        _res("a", HealthStatus.ok),
        _res("b", HealthStatus.degraded),
        _res("c", HealthStatus.ok),
    )
    assert aggregate(results) is HealthStatus.degraded


def test_aggregate_down_beats_degraded():
    results = (
        _res("a", HealthStatus.degraded),
        _res("b", HealthStatus.down),
    )
    assert aggregate(results) is HealthStatus.down


def test_empty_aggregate_is_down_fail_closed():
    assert aggregate(()) is HealthStatus.down


def test_registry_all_ok():
    reg = HealthRegistry()
    reg.register("db", lambda: _res("db", HealthStatus.ok))
    reg.register("cache", lambda: _res("cache", HealthStatus.ok))
    report = reg.run(now=NOW)
    assert report.status is HealthStatus.ok
    assert report.ts == NOW
    assert len(report.components) == 2


def test_registry_worst_component_wins():
    reg = HealthRegistry()
    reg.register("db", lambda: _res("db", HealthStatus.ok))
    reg.register("queue", lambda: _res("queue", HealthStatus.down))
    report = reg.run(now=NOW)
    assert report.status is HealthStatus.down


def test_raising_check_is_down():
    reg = HealthRegistry()

    def boom() -> HealthResult:
        raise RuntimeError("connection refused")

    reg.register("db", boom)
    report = reg.run()
    assert report.status is HealthStatus.down
    assert "check raised" in report.components[0].detail


def test_empty_registry_is_down():
    reg = HealthRegistry()
    assert reg.run().status is HealthStatus.down


def test_duplicate_registration_rejected():
    reg = HealthRegistry()
    reg.register("db", lambda: _res("db", HealthStatus.ok))
    with pytest.raises(ValueError):
        reg.register("db", lambda: _res("db", HealthStatus.ok))


def test_empty_name_rejected():
    reg = HealthRegistry()
    with pytest.raises(ValueError):
        reg.register("  ", lambda: _res("x", HealthStatus.ok))


def test_check_reporting_wrong_name_is_corrected():
    reg = HealthRegistry()
    reg.register("db", lambda: _res("something-else", HealthStatus.ok))
    report = reg.run()
    assert report.components[0].name == "db"
