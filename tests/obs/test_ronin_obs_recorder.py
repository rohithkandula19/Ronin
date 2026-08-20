"""The observer: one observe() call folds a real Event stream into metrics + logs.

This is the integration seam the parent bolts onto the loop's event stream, so the
events fed here are the genuine :mod:`ronin.core.types` values, not stand-ins.
"""

from __future__ import annotations

import json

from ronin.core.types import (
    Compaction,
    Error,
    Event,
    TextDelta,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.obs import SessionObserver


def _observer(lines: list[str], *, verbose: bool = False) -> SessionObserver:
    return SessionObserver(
        session_id="obs-1",
        sink=lines.append,
        verbose=verbose,
        ts_clock=lambda: 7.0,
    )


def _ok(tool_use_id: str, name: str) -> ToolEnd:
    return ToolEnd(tool_use_id=tool_use_id, name=name, result=ToolResult(ok=True, content="done"))


def _err(tool_use_id: str, name: str) -> ToolEnd:
    return ToolEnd(tool_use_id=tool_use_id, name=name, result=ToolResult(ok=False, error="boom"))


def test_observe_updates_turn_and_tool_metrics_from_real_events() -> None:
    obs = _observer([])
    stream: list[Event] = [
        TurnStart(turn_index=0),
        ToolStart(tool_use_id="t1", name="read_file"),
        _ok("t1", "read_file"),
        ToolStart(tool_use_id="t2", name="bash"),
        _err("t2", "bash"),
        TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls"),
    ]
    for event in stream:
        obs.observe(event)
    assert obs.metrics.turns == 1
    assert obs.metrics.tool_calls == 2
    assert obs.metrics.tool_errors == 1


def test_observe_counts_each_turn_start() -> None:
    obs = _observer([])
    obs.observe(TurnStart(turn_index=0))
    obs.observe(TurnStart(turn_index=1))
    obs.observe(TurnStart(turn_index=2))
    assert obs.metrics.turns == 3


def test_a_failed_tool_logs_at_warn() -> None:
    lines: list[str] = []
    _observer(lines).observe(_err("t1", "bash"))
    finished = [json.loads(line) for line in lines if json.loads(line)["event"] == "tool_finished"]
    assert len(finished) == 1
    assert finished[0]["level"] == "warn"
    assert finished[0]["ok"] is False
    assert finished[0]["session_id"] == "obs-1"


def test_observe_logs_compaction_and_errors() -> None:
    lines: list[str] = []
    obs = _observer(lines)
    obs.observe(Compaction(folded_messages=5, token_estimate_before=100, token_estimate_after=40))
    obs.observe(Error(message="disk full", kind="io", recoverable=False))
    records = [json.loads(line) for line in lines]
    events = {r["event"] for r in records}
    assert {"compaction", "error"} <= events
    error_line = next(r for r in records if r["event"] == "error")
    assert error_line["level"] == "error"
    assert error_line["kind"] == "io"


def test_every_observe_log_line_carries_the_session_id() -> None:
    lines: list[str] = []
    obs = _observer(lines, verbose=True)
    events: list[Event] = [
        TurnStart(turn_index=0),
        ToolStart(tool_use_id="t1", name="read_file"),
        _ok("t1", "read_file"),
        TextDelta(text="hi"),  # falls to the default case
        TurnEnd(turn_index=0, state=TurnState.DONE),
    ]
    for event in events:
        obs.observe(event)
    assert lines
    for line in lines:
        assert json.loads(line)["session_id"] == "obs-1"


def test_tokens_cost_and_cache_come_through_the_explicit_api() -> None:
    obs = _observer([])
    obs.tokens(120, 30)
    obs.tokens(80, 10)
    obs.cost(0.4)
    obs.cache(hit=True)
    obs.cache(hit=False)
    assert obs.metrics.input_tokens == 200
    assert obs.metrics.output_tokens == 40
    assert obs.metrics.cost_usd == 0.4
    assert obs.metrics.cache_hit_rate() == 0.5


async def test_the_ergonomic_span_api_produces_a_breakdown() -> None:
    lines: list[str] = []
    # timing clock: turn_started reads nothing; model span 0->0.5, tool span 0.5->0.9,
    # turn_finished reads 1.0 → model 0.5 / tool 0.4 / overhead 0.1.
    ticks = iter([0.0, 0.5, 0.5, 0.9, 1.0])
    obs = SessionObserver(
        session_id="obs-1",
        sink=lines.append,
        verbose=True,
        clock=lambda: next(ticks),
        ts_clock=lambda: 7.0,
    )
    obs.turn_started(0)
    async with obs.model_span():
        pass
    async with obs.tool_span("bash"):
        pass
    obs.turn_finished(0)
    records = [json.loads(line) for line in lines]
    finished = [r for r in records if r["event"] == "turn_finished"]
    assert len(finished) == 1
    assert finished[0]["breakdown"] == "model 0.5s / tool 0.4s / overhead 0.1s"
    # verbose surfaces the per-span debug lines too.
    span_lines = [r for r in records if r["event"] == "span"]
    assert {r["name"] for r in span_lines} == {"model", "bash"}


async def test_a_span_without_turn_started_opens_a_timer_lazily() -> None:
    # No turn_started(), so the span must open the per-turn timer on its own.
    ticks = iter([0.0, 0.5])  # model span 0.0 -> 0.5
    lines: list[str] = []
    obs = SessionObserver(
        session_id="obs-1",
        sink=lines.append,
        clock=lambda: next(ticks),
        ts_clock=lambda: 7.0,
    )
    async with obs.model_span() as span:
        pass
    assert span.seconds == 0.5


def test_observe_and_render_prometheus_together() -> None:
    obs = _observer([])
    obs.observe(TurnStart(turn_index=0))
    obs.observe(_ok("t1", "read_file"))
    text = obs.render_prometheus()
    assert "ronin_turns_total 1" in text
    assert "ronin_tool_calls_total 1" in text
    assert obs.snapshot()["turns"] == 1
