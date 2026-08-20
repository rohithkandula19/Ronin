"""Structured logging: one JSON object per line, correlated by session id.

Deliberately **not** a wrapper around the stdlib ``logging`` module. That module is a
process-global singleton whose handlers, levels and formatters are configured from
somewhere else entirely, and this layer is a *library other layers call*, not the thing
that owns the root logger. So a :class:`StructuredLogger` holds its own level, writes
through an injected :data:`Sink`, and touches no global state — a test captures its
output to a list, and two loggers in one process cannot stamp on each other's config.

``session_id`` is passed in at construction and never generated here. The correlation id
is the *real* session's id, which the parent supplies; a uuid minted in this module
would be a different id that correlates nothing, so there is no ``uuid`` import and no
hidden randomness.

Time is injected too (``clock``, default :func:`time.time`): the ``ts`` field is a real
field, so a test scripts it and asserts on it rather than matching a wildcard.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

#: Where one finished log line — a complete JSON object, no trailing newline — goes.
#: Injected so nothing in this layer is bound to stdout/stderr; the default writes to
#: stderr, and a test passes ``list.append`` to capture every line.
Sink = Callable[[str], None]


class LogLevel(IntEnum):
    """Severity as an ordered value, so a threshold is a comparison.

    ``IntEnum`` for the ordering (``level >= threshold``); the wire form is the
    lowercase name (:attr:`label`, ``"debug"``…``"error"``), so the numbers filter
    lines but never leak into one.
    """

    DEBUG = 10
    INFO = 20
    WARN = 30
    ERROR = 40

    @property
    def label(self) -> str:
        """The lowercase name, which is what appears in the ``level`` field."""
        return self.name.lower()


def write_stderr(line: str) -> None:
    """The default sink: one line to the *current* ``sys.stderr``.

    Reads ``sys.stderr`` at call time rather than binding it at import, so a caller
    that redirects it — or a test that captures it — is honoured.
    """
    sys.stderr.write(line + "\n")


def _dumps(record: Mapping[str, Any]) -> str:
    # ``default=str`` so a stray non-JSON value degrades to its repr instead of raising
    # out of a logger, and no ``sort_keys`` so the correlation fields (ts, level,
    # session_id, event) stay at the front of every line in insertion order.
    return json.dumps(record, ensure_ascii=False, default=str)


@dataclass(frozen=True, slots=True)
class StructuredLogger:
    """Emit ``{ts, level, session_id, event, ...fields}`` as one JSON line per call.

    Frozen and self-contained: the level and the verbosity are values on the instance,
    not global configuration, which is what lets the parent hand one logger to the loop
    and another to a replay without the two interfering.
    """

    session_id: str
    sink: Sink = write_stderr
    level: LogLevel = LogLevel.INFO
    verbose: bool = False
    clock: Callable[[], float] = time.time

    def enabled(self, level: LogLevel) -> bool:
        """Whether ``level`` clears the bar. ``verbose`` drops the bar to DEBUG."""
        threshold = LogLevel.DEBUG if self.verbose else self.level
        return level >= threshold

    def emit(
        self,
        level: LogLevel,
        event: str,
        fields: Mapping[str, Any] | None = None,
        *,
        detail: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Write one line and return the record, or ``None`` if the level is filtered.

        ``fields`` always appear. ``detail`` appears *only* under ``verbose`` — it is
        the "--debug" trace context (tool arguments, per-span seconds, running totals)
        that answers "why was this slow" without drowning an ordinary run in noise.
        """
        if not self.enabled(level):
            return None
        record: dict[str, Any] = {
            "ts": round(self.clock(), 6),
            "level": level.label,
            "session_id": self.session_id,
            "event": event,
        }
        if fields:
            record.update(fields)
        if self.verbose and detail:
            record.update(detail)
        self.sink(_dumps(record))
        return record

    def debug(self, event: str, **fields: Any) -> dict[str, Any] | None:
        return self.emit(LogLevel.DEBUG, event, fields)

    def info(self, event: str, **fields: Any) -> dict[str, Any] | None:
        return self.emit(LogLevel.INFO, event, fields)

    def warn(self, event: str, **fields: Any) -> dict[str, Any] | None:
        return self.emit(LogLevel.WARN, event, fields)

    def error(self, event: str, **fields: Any) -> dict[str, Any] | None:
        return self.emit(LogLevel.ERROR, event, fields)


__all__ = [
    "LogLevel",
    "Sink",
    "StructuredLogger",
    "write_stderr",
]
