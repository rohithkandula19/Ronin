"""The session observer: one object that logs, times and counts a whole session.

Ties a :class:`~ronin.obs.logs.StructuredLogger`, a :class:`~ronin.obs.timing.TurnTimer`
and a :class:`~ronin.obs.metrics.Metrics` to one ``session_id`` and exposes two ways in:

* the **ergonomic methods** — :meth:`turn_started`, :meth:`model_span`,
  :meth:`tool_span`, :meth:`tool_finished`, :meth:`tokens`, :meth:`cost`,
  :meth:`cache`, :meth:`turn_finished` — for the caller that *runs* the turn and can
  wrap the model and tool awaits, so it gets a timing breakdown as well as counts;
* :meth:`observe`, **one call fed every** ``Event`` — for a caller that only holds the
  event stream (a UI, a replay, the transcript tap). It updates metrics and logs, but
  not timing: the events say a tool ended, not how the wall clock split, and inventing
  a split from arrival order would be a number that lies.

Feed one instance through **one** of these paths. Both increment the same counters, so
a caller that both ``observe``\\ s the stream *and* calls :meth:`turn_started` by hand
counts every turn twice — a footgun stated here because the type system cannot.

Tokens, cost and cache-hit signals have no home in the core ``Event`` union — they ride
``FinalMessage`` and the provider's accounting, one layer up — so :meth:`observe` never
sees them. The parent feeds them through :meth:`tokens`, :meth:`cost` and :meth:`cache`
at the point in the loop that has the ``FinalMessage`` in hand.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from ronin.core.types import (
    Compaction,
    Error,
    Event,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
)

from .logs import LogLevel, Sink, StructuredLogger, write_stderr
from .metrics import Metrics
from .timing import ZERO_TIMING, Clock, Span, SpanKind, TurnTimer, TurnTiming


@dataclass(slots=True)
class SessionObserver:
    """Logger + timer + metrics, bound to one ``session_id``.

    ``clock`` measures durations (spans, wall) and defaults to a monotonic clock;
    ``ts_clock`` stamps log lines and defaults to wall time. Both are injected so a test
    is fully deterministic. The :class:`StructuredLogger` is built in
    :meth:`__post_init__` from these fields, and :attr:`metrics` is public so a caller
    can read counters, :meth:`~ronin.obs.metrics.Metrics.snapshot` or
    :meth:`~ronin.obs.metrics.Metrics.render_prometheus` at any time.
    """

    session_id: str
    sink: Sink = write_stderr
    verbose: bool = False
    level: LogLevel = LogLevel.INFO
    clock: Clock = time.monotonic
    ts_clock: Callable[[], float] = time.time
    metrics: Metrics = field(default_factory=Metrics)
    logger: StructuredLogger = field(init=False)
    _timer: TurnTimer | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.logger = StructuredLogger(
            session_id=self.session_id,
            sink=self.sink,
            level=self.level,
            verbose=self.verbose,
            clock=self.ts_clock,
        )

    # --------------------------------------------------------------- the one-call seam

    def observe(self, event: Event) -> None:
        """Fold one loop ``Event`` into metrics and logs. The parent's single wire-in.

        Handles what the event actually carries; tokens/cost/cache arrive through the
        explicit methods (see the module docstring). Timing is not done here — use the
        span methods for that.
        """
        match event:
            case TurnStart():
                self._count_turn(event.turn_index)
            case ToolStart():
                self.logger.emit(
                    LogLevel.DEBUG,
                    "tool_started",
                    {"tool": event.name, "tool_use_id": event.tool_use_id},
                    detail={"arguments": dict(event.arguments)},
                )
            case ToolEnd():
                self.tool_finished(event.name, event.result.ok)
            case TurnEnd():
                self._log_turn_finished(
                    event.turn_index,
                    None,
                    state=event.state.value,
                    stop_reason=event.stop_reason,
                )
            case Compaction():
                self.logger.info(
                    "compaction",
                    folded_messages=event.folded_messages,
                    tokens_before=event.token_estimate_before,
                    tokens_after=event.token_estimate_after,
                )
            case Error():
                self.logger.error(
                    "error",
                    kind=event.kind,
                    message=event.message,
                    recoverable=event.recoverable,
                )
            case _:
                # TextDelta, StreamReset, ApprovalRequest, VerifyResult: nothing to
                # count, but a verbose trace still wants to see them go by.
                self.logger.debug("event", kind=type(event).__name__)

    # ----------------------------------------------------------------------- turns

    def turn_started(self, index: int) -> None:
        """Begin a turn: count it, log it, and open a fresh timer for its spans."""
        self._count_turn(index)
        self._timer = TurnTimer(clock=self.clock)

    def turn_finished(self, index: int, *, state: str = "", stop_reason: str = "") -> None:
        """End a turn: settle the timer and log the timing breakdown."""
        timing = ZERO_TIMING if self._timer is None else self._timer.finish()
        self._timer = None
        self._log_turn_finished(index, timing, state=state, stop_reason=stop_reason)

    # ----------------------------------------------------------------------- spans

    def model_span(self) -> AbstractAsyncContextManager[Span]:
        """Time an awaited model call: ``async with observer.model_span(): ...``."""
        return self._span(SpanKind.MODEL, "model")

    def tool_span(self, name: str) -> AbstractAsyncContextManager[Span]:
        """Time an awaited tool call: ``async with observer.tool_span(name): ...``."""
        return self._span(SpanKind.TOOL, name)

    @asynccontextmanager
    async def _span(self, kind: SpanKind, name: str) -> AsyncIterator[Span]:
        timer = self._ensure_timer()
        async with timer.aspan(kind) as span:
            yield span
        self.logger.emit(
            LogLevel.DEBUG,
            "span",
            {"kind": kind.value, "name": name, "seconds": round(span.seconds, 6)},
        )

    def _ensure_timer(self) -> TurnTimer:
        if self._timer is None:
            self._timer = TurnTimer(clock=self.clock)
        return self._timer

    # --------------------------------------------------------------- tools / usage

    def tool_finished(self, name: str, ok: bool) -> None:
        """Record one tool outcome. A failure logs at WARN, a success at INFO."""
        self.metrics.record_tool(ok)
        level = LogLevel.INFO if ok else LogLevel.WARN
        self.logger.emit(
            level,
            "tool_finished",
            {"tool": name, "ok": ok},
            detail={
                "tool_calls": self.metrics.tool_calls,
                "tool_errors": self.metrics.tool_errors,
            },
        )

    def tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Record one response's prompt/completion token counts."""
        self.metrics.record_tokens(input_tokens, output_tokens)
        self.logger.emit(
            LogLevel.INFO,
            "tokens",
            {"input": input_tokens, "output": output_tokens},
            detail={
                "input_total": self.metrics.input_tokens,
                "output_total": self.metrics.output_tokens,
            },
        )

    def cost(self, usd: float) -> None:
        """Record one response's cost in USD."""
        self.metrics.add_cost(usd)
        self.logger.emit(
            LogLevel.INFO,
            "cost",
            {"usd": round(usd, 6)},
            detail={"cost_total": round(self.metrics.cost_usd, 6)},
        )

    def cache(self, hit: bool) -> None:
        """Record one prompt-cache lookup as a hit or a miss."""
        self.metrics.record_cache(hit)
        self.logger.emit(
            LogLevel.DEBUG,
            "cache",
            {"hit": hit},
            detail={"hit_rate": round(self.metrics.cache_hit_rate(), 6)},
        )

    # --------------------------------------------------------------- pass-throughs

    def snapshot(self) -> dict[str, float]:
        """The current metrics as a plain dict."""
        return self.metrics.snapshot()

    def render_prometheus(self) -> str:
        """The current metrics as prometheus text exposition."""
        return self.metrics.render_prometheus()

    # ----------------------------------------------------------------------- shared

    def _count_turn(self, index: int) -> None:
        self.metrics.record_turn()
        self.logger.info("turn_started", turn=index)

    def _log_turn_finished(
        self,
        index: int,
        timing: TurnTiming | None,
        *,
        state: str = "",
        stop_reason: str = "",
    ) -> None:
        fields: dict[str, Any] = {"turn": index}
        if state:
            fields["state"] = state
        if stop_reason:
            fields["stop_reason"] = stop_reason
        detail: dict[str, Any] | None = None
        if timing is not None:
            fields["breakdown"] = timing.breakdown()
            detail = timing.snapshot()
        self.logger.emit(LogLevel.INFO, "turn_finished", fields, detail=detail)


__all__ = [
    "SessionObserver",
]
