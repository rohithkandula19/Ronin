"""Structured logging with a mandatory redaction filter.

Records are pure data (pydantic models) written into an injectable in-memory
sink. There is no network exporter and no stdout/file handler — this module
only *models* logs. A :class:`Redactor` runs on every record before it reaches
the sink: values under sensitive keys are replaced wholesale, and free-text
values (message + string fields) are scrubbed for secret/credential/token and
PII patterns. Detection is pattern-based and generic; no real secret is ever
embedded here.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

REDACTED = "***"


class LogLevel(str, Enum):
    debug = "debug"
    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


# Ordering used for level threshold filtering.
_LEVEL_ORDER: dict[LogLevel, int] = {
    LogLevel.debug: 10,
    LogLevel.info: 20,
    LogLevel.warning: 30,
    LogLevel.error: 40,
    LogLevel.critical: 50,
}


class LogRecord(BaseModel):
    """One structured log line. Immutable data, no behaviour."""

    model_config = ConfigDict(extra="forbid")

    level: LogLevel
    message: str
    fields: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""  # caller-supplied correlation id (not scrubbed)
    span_id: str = ""  # caller-supplied correlation id (not scrubbed)
    ts: str = ""  # caller-supplied ISO timestamp (deterministic tests)


@runtime_checkable
class LogSink(Protocol):
    """Anything that can accept an emitted (already-redacted) record."""

    def emit(self, record: LogRecord) -> None: ...


class InMemoryLogSink:
    """Default sink: keeps every emitted record in a list. No I/O."""

    def __init__(self) -> None:
        self.records: list[LogRecord] = []

    def emit(self, record: LogRecord) -> None:
        self.records.append(record)

    def clear(self) -> None:
        self.records.clear()

    def __len__(self) -> int:
        return len(self.records)


# Substrings that mark a *key* as sensitive. If a field key contains any of
# these (case-insensitive), the whole value is replaced regardless of shape.
# Deliberately broad — over-redaction is the safe failure mode.
_SENSITIVE_KEY_MARKERS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "authorization",
    "auth",
    "credential",
    "cred",
    "private",
    "session",
    "cookie",
    "signature",
    "key",
    # PII / identity
    "ssn",
    "sin",
    "dob",
    "birth",
    "email",
    "phone",
    "card",
    "cvv",
    "pin",
    # healthcare / PHI markers
    "patient",
    "mrn",
    "diagnosis",
    "phi",
    "medical",
    "health",
    "insurance",
)

# Value-shaped patterns scrubbed from any free-text string. These describe the
# *shape* of sensitive data; they contain no real credentials.
_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # email address
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    # bearer / authorization prefix followed by a token
    re.compile(r"(?i)\bbearer\s+\S+"),
    # US-SSN shape ddd-dd-dddd
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    # credit-card shape (13-16 digits, optional separators)
    re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    # JWT-like: three base64url segments joined by dots
    re.compile(r"\b[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\b"),
    # long high-entropy token (base64url / hex alphabet, 24+ chars)
    re.compile(r"\b[A-Za-z0-9_\-]{24,}\b"),
)


class Redactor:
    """Scrubs records fail-closed before they reach a sink.

    Two layers:
      * key-based: any field whose key contains a sensitive marker has its
        value replaced by ``***`` entirely (nested dicts included);
      * value-based: any remaining string is matched against shape patterns
        (email, bearer, SSN, card, JWT, long token) and matches replaced.
    """

    def __init__(
        self,
        *,
        sensitive_key_markers: tuple[str, ...] = _SENSITIVE_KEY_MARKERS,
        value_patterns: tuple[re.Pattern[str], ...] = _VALUE_PATTERNS,
        placeholder: str = REDACTED,
    ) -> None:
        self._markers = tuple(m.lower() for m in sensitive_key_markers)
        self._patterns = value_patterns
        self._placeholder = placeholder

    def is_sensitive_key(self, key: str) -> bool:
        low = key.lower()
        return any(marker in low for marker in self._markers)

    def scrub_text(self, text: str) -> str:
        """Replace every shape-matched span in *text* with the placeholder."""
        out = text
        for pattern in self._patterns:
            out = pattern.sub(self._placeholder, out)
        return out

    def scrub_value(self, key: str, value: Any) -> Any:
        # Key alone condemns the value, whatever its shape.
        if self.is_sensitive_key(key):
            return self._placeholder
        if isinstance(value, str):
            return self.scrub_text(value)
        if isinstance(value, dict):
            return {k: self.scrub_value(str(k), v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            scrubbed = [self.scrub_value(key, v) for v in value]
            return type(value)(scrubbed)
        return value

    def scrub_fields(self, fields: dict[str, Any]) -> dict[str, Any]:
        return {k: self.scrub_value(str(k), v) for k, v in fields.items()}

    def scrub_record(self, record: LogRecord) -> LogRecord:
        return record.model_copy(
            update={
                "message": self.scrub_text(record.message),
                "fields": self.scrub_fields(record.fields),
            }
        )


class StructuredLogger:
    """Emits redacted structured records into an injectable sink.

    Deterministic: the caller supplies ``now`` and the correlation ids; the
    logger reads no clock and generates no ids.
    """

    def __init__(
        self,
        sink: LogSink | None = None,
        *,
        redactor: Redactor | None = None,
        min_level: LogLevel = LogLevel.debug,
    ) -> None:
        self.sink: LogSink = sink if sink is not None else InMemoryLogSink()
        self.redactor = redactor if redactor is not None else Redactor()
        self.min_level = min_level

    def _enabled(self, level: LogLevel) -> bool:
        return _LEVEL_ORDER[level] >= _LEVEL_ORDER[self.min_level]

    def log(
        self,
        level: LogLevel,
        message: str,
        *,
        now: str,
        trace_id: str = "",
        span_id: str = "",
        **fields: Any,
    ) -> LogRecord | None:
        """Build, redact, and emit a record. Returns it, or None if filtered."""
        if not self._enabled(level):
            return None
        record = LogRecord(
            level=level,
            message=message,
            fields=fields,
            trace_id=trace_id,
            span_id=span_id,
            ts=now,
        )
        safe = self.redactor.scrub_record(record)
        self.sink.emit(safe)
        return safe

    def debug(self, message: str, *, now: str, **kw: Any) -> LogRecord | None:
        return self.log(LogLevel.debug, message, now=now, **kw)

    def info(self, message: str, *, now: str, **kw: Any) -> LogRecord | None:
        return self.log(LogLevel.info, message, now=now, **kw)

    def warning(self, message: str, *, now: str, **kw: Any) -> LogRecord | None:
        return self.log(LogLevel.warning, message, now=now, **kw)

    def error(self, message: str, *, now: str, **kw: Any) -> LogRecord | None:
        return self.log(LogLevel.error, message, now=now, **kw)

    def critical(self, message: str, *, now: str, **kw: Any) -> LogRecord | None:
        return self.log(LogLevel.critical, message, now=now, **kw)
