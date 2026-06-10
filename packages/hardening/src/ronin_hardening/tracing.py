from __future__ import annotations

import re
import time
import uuid
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field


PII_PATTERNS: list[tuple[str, str]] = [
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[email]"),
    (r"\b\d{3}-\d{2}-\d{4}\b", "[ssn]"),
    (r"\b(?:\d[ -]*?){13,19}\b", "[card]"),
    (r"\bsk-[A-Za-z0-9]{20,}\b", "[api-key]"),
]


def redact_pii(text: str, extra_patterns: list[tuple[str, str]] | None = None) -> str:
    """Replace common PII / secret patterns with placeholders. Best-effort, never bullet-proof."""
    out = text
    for pattern, replacement in (PII_PATTERNS + (extra_patterns or [])):
        out = re.sub(pattern, replacement, out)
    return out


class TraceEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str
    parent_id: str | None = None
    name: str
    kind: str  # "agent_start", "tool_call", "tool_result", "model_call", "agent_end"
    timestamp: float = Field(default_factory=time.time)
    payload: dict[str, Any] = Field(default_factory=dict)


class TraceEmitter(BaseModel):
    """Provider-agnostic trace emitter.

    Pass a ``sink`` callable that forwards events to your observability backend
    (Langfuse, Helicone, OpenTelemetry, stdout, your own DB). Events are PII-redacted
    before emission unless ``redact=False``.

    Compatible with Langfuse-shaped spans by mapping ``kind`` → span type in your sink.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    sink: Callable[[TraceEvent], None]
    redact: bool = True
    extra_pii_patterns: list[tuple[str, str]] = Field(default_factory=list)

    def start_trace(self, name: str, payload: dict[str, Any] | None = None) -> str:
        trace_id = str(uuid.uuid4())
        self.emit(trace_id, "agent_start", name, payload or {}, parent_id=None)
        return trace_id

    def end_trace(self, trace_id: str, name: str, payload: dict[str, Any] | None = None) -> None:
        self.emit(trace_id, "agent_end", name, payload or {}, parent_id=None)

    def emit(
        self,
        trace_id: str,
        kind: str,
        name: str,
        payload: dict[str, Any],
        parent_id: str | None = None,
    ) -> TraceEvent:
        clean = self._redact_payload(payload) if self.redact else payload
        event = TraceEvent(trace_id=trace_id, parent_id=parent_id, name=name, kind=kind, payload=clean)
        self.sink(event)
        return event

    def _redact_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in payload.items():
            if isinstance(v, str):
                out[k] = redact_pii(v, self.extra_pii_patterns)
            elif isinstance(v, dict):
                out[k] = self._redact_payload(v)
            elif isinstance(v, list):
                out[k] = [
                    redact_pii(x, self.extra_pii_patterns) if isinstance(x, str)
                    else self._redact_payload(x) if isinstance(x, dict)
                    else x
                    for x in v
                ]
            else:
                out[k] = v
        return out
