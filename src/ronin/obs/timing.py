"""Per-turn timing: where the wall clock went, split model / tool / overhead.

The clock is injected (:data:`Clock`, default :func:`time.monotonic`) so a test scripts
it and the arithmetic is exact — there is no hidden ``time.monotonic()`` call anywhere
in this module, which is the whole reason a "why was this turn slow" number can be
trusted rather than merely printed.

``overhead`` is **derived, never measured**: it is ``wall - (model + tool)`` — every
second the turn spent neither awaiting the model nor running a tool (scheduling,
serialization, our own code between the two). Measuring it directly would let it
disagree with the wall clock; deriving it guarantees the three parts sum to the whole.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from enum import StrEnum

#: A monotonic-ish clock returning seconds. Injected everywhere; never called at import
#: or construction time — only inside a span and once at :meth:`TurnTimer.finish`.
Clock = Callable[[], float]


class SpanKind(StrEnum):
    """The two things a turn spends time on that we time directly.

    Not an open set: overhead is *everything else*, found by subtraction, so a third
    kind here would silently come out of overhead and break the identity that the parts
    sum to the wall clock. ``SpanKind(value)`` raises on anything else, which is how a
    typo in ``span("modle")`` fails loudly instead of being charged to overhead.
    """

    MODEL = "model"
    TOOL = "tool"


@dataclass(slots=True)
class Span:
    """A handle a span yields so the caller can read its duration afterwards.

    ``seconds`` is ``0.0`` until the ``with`` block exits; it is filled in the span's
    ``finally`` from the same two clock reads that feed the timer, so reading it costs
    no extra clock call.
    """

    kind: SpanKind
    seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class TurnTiming:
    """The answer to "where did the time go", as four numbers that sum correctly."""

    model_seconds: float
    tool_seconds: float
    overhead_seconds: float
    wall_seconds: float

    def breakdown(self) -> str:
        """One line, one decimal each: ``model 1.2s / tool 0.4s / overhead 0.1s``."""
        return (
            f"model {self.model_seconds:.1f}s / "
            f"tool {self.tool_seconds:.1f}s / "
            f"overhead {self.overhead_seconds:.1f}s"
        )

    def snapshot(self) -> dict[str, float]:
        """The four numbers as a plain dict, for a log line or a test."""
        return {
            "model_seconds": self.model_seconds,
            "tool_seconds": self.tool_seconds,
            "overhead_seconds": self.overhead_seconds,
            "wall_seconds": self.wall_seconds,
        }


#: A turn that timed nothing — what :meth:`TurnTimer.finish` returns when no span ran,
#: rather than inventing a wall time from a clock it never started.
ZERO_TIMING = TurnTiming(0.0, 0.0, 0.0, 0.0)


@dataclass(slots=True)
class TurnTimer:
    """Accumulates model and tool time across a turn; derives overhead at the end.

    Reads the clock exactly twice per span (enter, exit) and once at :meth:`finish`. The
    first span's enter is the wall start, so a timer with no spans has no start and
    :meth:`finish` returns :data:`ZERO_TIMING`. Mutable by design — one timer per turn,
    fed by the spans as they open and close.
    """

    clock: Clock = time.monotonic
    _start: float | None = field(default=None, init=False)
    _model: float = field(default=0.0, init=False)
    _tool: float = field(default=0.0, init=False)

    @contextmanager
    def span(self, kind: SpanKind | str) -> Iterator[Span]:
        """Time a block synchronously. ``kind`` is ``"model"``/``"tool"`` or a SpanKind."""
        handle = Span(kind=SpanKind(kind))
        start = self.clock()
        if self._start is None:
            self._start = start
        try:
            yield handle
        finally:
            handle.seconds = self.clock() - start
            self._add(handle)

    @asynccontextmanager
    async def aspan(self, kind: SpanKind | str) -> AsyncIterator[Span]:
        """The ``async with`` form — same accounting, for awaiting a model or tool call."""
        handle = Span(kind=SpanKind(kind))
        start = self.clock()
        if self._start is None:
            self._start = start
        try:
            yield handle
        finally:
            handle.seconds = self.clock() - start
            self._add(handle)

    def _add(self, handle: Span) -> None:
        if handle.kind is SpanKind.MODEL:
            self._model += handle.seconds
        else:
            self._tool += handle.seconds

    def finish(self) -> TurnTiming:
        """Read the clock once more and settle the four numbers. Call once, at turn end.

        ``overhead`` is clamped at ``0.0``: with a real monotonic clock it never goes
        negative, and clamping keeps a float-rounding wobble from rendering a nonsense
        ``overhead -0.0s`` in the one number a user reads to explain a slow turn.
        """
        if self._start is None:
            return ZERO_TIMING
        wall = self.clock() - self._start
        overhead = wall - self._model - self._tool
        return TurnTiming(
            model_seconds=self._model,
            tool_seconds=self._tool,
            overhead_seconds=max(0.0, overhead),
            wall_seconds=wall,
        )


__all__ = [
    "ZERO_TIMING",
    "Clock",
    "Span",
    "SpanKind",
    "TurnTimer",
    "TurnTiming",
]
