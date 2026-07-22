"""Ronin Observability — logs, metrics, tracing, SLOs, and health.

Everything here is in-memory and deterministic: sinks and registries hold data
in process, callers supply timestamps and correlation ids, and nothing touches
the network, stdout, or a random source. The structured logger runs a mandatory
redaction filter so secret/credential/token and PII shapes never reach a sink.
"""

from __future__ import annotations

from .health import (
    HealthCheck,
    HealthRegistry,
    HealthReport,
    HealthResult,
    HealthStatus,
    aggregate,
)
from .logs import (
    REDACTED,
    InMemoryLogSink,
    LogLevel,
    LogRecord,
    LogSink,
    Redactor,
    StructuredLogger,
)
from .metrics import (
    CardinalityGuard,
    Histogram,
    MetricError,
    MetricsRegistry,
)
from .slo import SLO, SLOReport, SLOStatus
from .trace import Span, SpanScope, Tracer

__all__ = [
    # logs
    "REDACTED",
    "InMemoryLogSink",
    "LogLevel",
    "LogRecord",
    "LogSink",
    "Redactor",
    "StructuredLogger",
    # metrics
    "CardinalityGuard",
    "Histogram",
    "MetricError",
    "MetricsRegistry",
    # trace
    "Span",
    "SpanScope",
    "Tracer",
    # slo
    "SLO",
    "SLOReport",
    "SLOStatus",
    # health
    "HealthCheck",
    "HealthRegistry",
    "HealthReport",
    "HealthResult",
    "HealthStatus",
    "aggregate",
]
