from __future__ import annotations

from ronin_hardening import TraceEmitter, redact_pii
from ronin_hardening.tracing import TraceEvent


def test_redact_pii_email_and_secret() -> None:
    text = "contact alice@example.com or use sk-abcdefghijklmnopqrstuvwxyz12345"
    redacted = redact_pii(text)
    assert "[email]" in redacted
    assert "[api-key]" in redacted
    assert "alice@example.com" not in redacted


def test_emitter_captures_events() -> None:
    captured: list[TraceEvent] = []
    emitter = TraceEmitter(sink=captured.append)

    trace_id = emitter.start_trace("research-agent", payload={"user": "alice@example.com"})
    emitter.emit(trace_id, "tool_call", "search", {"query": "weather"})
    emitter.end_trace(trace_id, "research-agent", {"output": "sunny"})

    assert len(captured) == 3
    assert captured[0].kind == "agent_start"
    # PII redacted
    assert captured[0].payload["user"] == "[email]"
    assert captured[1].kind == "tool_call"
    assert captured[2].kind == "agent_end"


def test_emitter_redact_off() -> None:
    captured: list[TraceEvent] = []
    emitter = TraceEmitter(sink=captured.append, redact=False)
    emitter.emit("t1", "tool_call", "echo", {"value": "alice@example.com"})
    assert captured[0].payload["value"] == "alice@example.com"


def test_extra_pii_patterns_apply() -> None:
    captured: list[TraceEvent] = []
    emitter = TraceEmitter(
        sink=captured.append,
        extra_pii_patterns=[(r"INTERNAL-\d+", "[internal-id]")],
    )
    emitter.emit("t1", "tool_call", "lookup", {"q": "user INTERNAL-12345 needs help"})
    assert "[internal-id]" in captured[0].payload["q"]
