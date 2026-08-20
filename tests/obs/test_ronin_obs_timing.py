"""Timing: exact model / tool / overhead / wall from a scripted clock, and breakdown."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from ronin.obs import SpanKind, TurnTimer, TurnTiming


def scripted(ticks: list[float]) -> Callable[[], float]:
    """A clock that returns each value in turn — deterministic, no real time."""
    remaining = list(ticks)

    def clock() -> float:
        return remaining.pop(0)

    return clock


def test_model_tool_overhead_and_wall_are_computed_exactly() -> None:
    # reads: model enter 0.0, model exit 0.5, tool enter 0.5, tool exit 0.9, finish 1.0
    timer = TurnTimer(clock=scripted([0.0, 0.5, 0.5, 0.9, 1.0]))
    with timer.span("model") as model:
        pass
    with timer.span("tool") as tool:
        pass
    timing = timer.finish()
    assert model.seconds == 0.5
    assert tool.seconds == pytest.approx(0.4)
    assert timing.model_seconds == 0.5
    assert timing.tool_seconds == pytest.approx(0.4)
    assert timing.overhead_seconds == pytest.approx(0.1)
    assert timing.wall_seconds == 1.0


def test_breakdown_renders_one_decimal() -> None:
    timing = TurnTiming(model_seconds=1.2, tool_seconds=0.4, overhead_seconds=0.1, wall_seconds=1.7)
    assert timing.breakdown() == "model 1.2s / tool 0.4s / overhead 0.1s"


def test_breakdown_of_the_scripted_turn() -> None:
    timer = TurnTimer(clock=scripted([0.0, 0.5, 0.5, 0.9, 1.0]))
    with timer.span("model"):
        pass
    with timer.span("tool"):
        pass
    assert timer.finish().breakdown() == "model 0.5s / tool 0.4s / overhead 0.1s"


async def test_the_async_span_accounts_the_same_way() -> None:
    # reads: enter 0.0, exit 2.0, finish 2.0 → model 2.0, wall 2.0, overhead 0.0
    timer = TurnTimer(clock=scripted([0.0, 2.0, 2.0]))
    async with timer.aspan(SpanKind.MODEL) as model:
        pass
    timing = timer.finish()
    assert model.seconds == 2.0
    assert timing.model_seconds == 2.0
    assert timing.wall_seconds == 2.0
    assert timing.overhead_seconds == 0.0


def test_two_model_spans_accumulate() -> None:
    # two model spans of 1s each, then finish at 3.0 → model 2, overhead 1, wall 3
    timer = TurnTimer(clock=scripted([0.0, 1.0, 2.0, 3.0, 3.0]))
    with timer.span("model"):
        pass
    with timer.span("model"):
        pass
    timing = timer.finish()
    assert timing.model_seconds == 2.0
    assert timing.tool_seconds == 0.0
    assert timing.overhead_seconds == 1.0
    assert timing.wall_seconds == 3.0


def test_an_unknown_span_kind_is_rejected() -> None:
    timer = TurnTimer(clock=scripted([]))
    with pytest.raises(ValueError), timer.span("gpu"):
        pass


def test_a_timer_with_no_spans_reports_zero() -> None:
    timer = TurnTimer(clock=scripted([]))
    assert timer.finish() == TurnTiming(0.0, 0.0, 0.0, 0.0)
