"""``ronin.obs`` — observability: structured logs, per-turn timing, session metrics.

A leaf. It imports :mod:`ronin.core.types` (the events and values it observes) and the
standard library, and **nothing else** — not providers, tools, agents, mcp, cli, ui,
ext or evals. It is an observer: data comes in, nothing is reached for. That purity is
what lets the parent place it at the bottom of the layer graph, and what lets every
test here run with a fake clock and a list for a sink — no model, no network, no global
logger.

For any slow session, this answers "where did the time go":

- :class:`StructuredLogger` — one JSON object per line, every line carrying the
  per-session correlation id (``session_id``), levels, and a ``verbose`` trace mode.
- :class:`TurnTimer` / :class:`TurnTiming` — a per-turn model / tool / overhead split,
  computed from an injected clock, with a one-line :meth:`~TurnTiming.breakdown`.
- :class:`Metrics` — counters for turns, tokens, cost, tool calls/errors and cache
  hits/misses, a :meth:`~Metrics.cache_hit_rate`, a :meth:`~Metrics.snapshot` and
  prometheus-style :meth:`~Metrics.render_prometheus` text.

:class:`SessionObserver` ties the three to one session and takes the loop's event
stream with a single :meth:`~SessionObserver.observe` call.
"""

from __future__ import annotations

from .logs import LogLevel, Sink, StructuredLogger, write_stderr
from .metrics import PROMETHEUS_METRICS, Metrics, MetricSpec
from .recorder import SessionObserver
from .timing import ZERO_TIMING, Clock, Span, SpanKind, TurnTimer, TurnTiming

__all__ = [
    "PROMETHEUS_METRICS",
    "ZERO_TIMING",
    "Clock",
    "LogLevel",
    "MetricSpec",
    "Metrics",
    "SessionObserver",
    "Sink",
    "Span",
    "SpanKind",
    "StructuredLogger",
    "TurnTimer",
    "TurnTiming",
    "write_stderr",
]
