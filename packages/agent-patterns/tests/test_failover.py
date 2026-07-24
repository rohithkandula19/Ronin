"""Tests for FailoverProvider — survive a backend going down by advancing to the
next configured provider."""
from __future__ import annotations

from typing import Iterator

import pytest

from ronin_agent_patterns import (
    FailoverProvider,
    FakeProvider,
    LLMResponse,
    Message,
    StreamEvent,
)
from ronin_agent_patterns.providers.base import LLMProvider


class _Boom(LLMProvider):
    """A provider that always fails — stands in for a rate-limited / down backend."""

    model: str = "boom"

    def complete(self, **_kw) -> LLMResponse:
        raise RuntimeError("503 service unavailable")

    def stream(self, **_kw) -> Iterator[StreamEvent]:
        raise RuntimeError("503 service unavailable")
        yield  # pragma: no cover — makes this a generator


class _BoomMidStream(LLMProvider):
    """Emits one token, then dies — to test that we DON'T silently restart."""

    model: str = "boom-mid"

    def complete(self, **_kw) -> LLMResponse:
        raise RuntimeError("died")

    def stream(self, **_kw) -> Iterator[StreamEvent]:
        yield StreamEvent(type="text", text="par")
        raise RuntimeError("died after first token")


def _ok(text: str) -> FakeProvider:
    return FakeProvider(model="ok", responses=[LLMResponse(text=text)])


def _args():
    return {"system": "s", "messages": [Message(role="user", content="hi")], "tools": []}


def test_complete_uses_primary_when_healthy() -> None:
    fo = FailoverProvider(providers=[_ok("primary"), _ok("backup")], labels=["a", "b"])
    resp = fo.complete(**_args())
    assert resp.text == "primary"
    assert fo.last_used == "a"
    assert fo.failed_over_to is None


def test_complete_fails_over_to_next() -> None:
    fo = FailoverProvider(providers=[_Boom(), _ok("rescued")], labels=["claude", "gemini"])
    resp = fo.complete(**_args())
    assert resp.text == "rescued"
    assert fo.last_used == "gemini"
    assert fo.failed_over_to == "gemini"


def test_complete_raises_when_all_fail() -> None:
    fo = FailoverProvider(providers=[_Boom(), _Boom()], labels=["a", "b"])
    with pytest.raises(RuntimeError, match="all 2 providers failed"):
        fo.complete(**_args())


def test_stream_fails_over_before_any_token() -> None:
    fo = FailoverProvider(providers=[_Boom(), _ok("streamed answer")], labels=["a", "b"])
    events = list(fo.stream(**_args()))
    text = "".join(e.text for e in events if e.type == "text")
    assert text == "streamed answer"
    assert fo.failed_over_to == "b"


def test_stream_resets_then_fails_over_after_emitting() -> None:
    """After tokens have streamed, a mid-stream failure now emits a reset (telling
    the renderer to discard the partial) and fails over — recovering the turn
    instead of losing it once a single token had streamed (F3)."""
    fo = FailoverProvider(providers=[_BoomMidStream(), _ok("backup answer")], labels=["a", "b"])
    events = list(fo.stream(**_args()))
    kinds = [e.type for e in events]
    assert "reset" in kinds                       # provider A's partial discarded
    # B's answer streams after the reset (FakeProvider emits it word-by-word).
    reset_at = kinds.index("reset")
    after = "".join(e.text or "" for e in events[reset_at:] if e.type == "text")
    assert "backup" in after and "answer" in after
    assert events[-1].type == "done"
    assert fo.failed_over_to == "b"


def test_empty_providers_raises() -> None:
    fo = FailoverProvider(providers=[], labels=[])
    with pytest.raises(RuntimeError, match="no providers"):
        fo.complete(**_args())
