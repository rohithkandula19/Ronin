"""A minimal span model and an in-memory tracer.

Spans carry caller-supplied numeric timestamps (epoch seconds or any
monotonic unit the caller chooses). Duration is derived arithmetically from
``start``/``end`` — the module never reads a wall clock and never generates
ids. Ids and timestamps come from the caller for deterministic tests.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Span(BaseModel):
    """A single unit of work in a trace."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    span_id: str
    name: str
    parent_id: str = ""
    start: float  # caller-supplied timestamp
    end: float | None = None  # None while the span is open
    attributes: dict[str, Any] = Field(default_factory=dict)

    def duration(self) -> float | None:
        """End minus start, or ``None`` if the span is still open."""
        if self.end is None:
            return None
        return self.end - self.start

    def is_open(self) -> bool:
        return self.end is None


class SpanScope:
    """Context helper that records a span into a tracer on exit.

    Usage::

        with tracer.span(trace_id="t", span_id="s", name="op", start=0.0) as sc:
            ...
            sc.stop(end=1.5)

    ``stop`` sets the caller-supplied end time; exiting the block records the
    span (whether or not it was stopped — an unstopped span is recorded open).
    """

    def __init__(self, tracer: "Tracer", span: Span) -> None:
        self._tracer = tracer
        self.span = span
        self._recorded = False

    def stop(self, *, end: float) -> Span:
        self.span.end = end
        return self.span

    def set_attribute(self, key: str, value: Any) -> None:
        self.span.attributes[key] = value

    def __enter__(self) -> "SpanScope":
        return self

    def __exit__(self, *exc: object) -> bool:
        if not self._recorded:
            self._tracer.record(self.span)
            self._recorded = True
        return False  # never suppress exceptions


class Tracer:
    """Collects spans in memory."""

    def __init__(self) -> None:
        self.spans: list[Span] = []

    def record(self, span: Span) -> Span:
        self.spans.append(span)
        return span

    def start(
        self,
        *,
        trace_id: str,
        span_id: str,
        name: str,
        start: float,
        parent_id: str = "",
        attributes: dict[str, Any] | None = None,
    ) -> Span:
        """Build a span (not yet recorded) with the given start time."""
        return Span(
            trace_id=trace_id,
            span_id=span_id,
            name=name,
            parent_id=parent_id,
            start=start,
            attributes=dict(attributes or {}),
        )

    def span(
        self,
        *,
        trace_id: str,
        span_id: str,
        name: str,
        start: float,
        parent_id: str = "",
        attributes: dict[str, Any] | None = None,
    ) -> SpanScope:
        """Open a :class:`SpanScope` for use as a context manager."""
        span = self.start(
            trace_id=trace_id,
            span_id=span_id,
            name=name,
            start=start,
            parent_id=parent_id,
            attributes=attributes,
        )
        return SpanScope(self, span)

    def trace_spans(self, trace_id: str) -> tuple[Span, ...]:
        return tuple(s for s in self.spans if s.trace_id == trace_id)
