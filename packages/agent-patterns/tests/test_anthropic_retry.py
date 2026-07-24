"""F3: the Anthropic provider must retry transient errors and reset mid-stream,
matching the OpenAI-compat path. Plus the failover relaxation (emit reset, then
fail over) so a single streamed token no longer forfeits the whole turn."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest

from ronin_agent_patterns import AnthropicProvider, FailoverProvider, Message, LLMResponse
from ronin_agent_patterns.providers.base import LLMProvider, StreamEvent


def _resp(text="ok"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def _status_error(code, retry_after=None):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    headers = {"retry-after": str(retry_after)} if retry_after else {}
    resp = httpx.Response(code, request=req, headers=headers)
    return anthropic.APIStatusError("boom", response=resp, body=None)


class _FakeStream:
    def __init__(self, deltas, final, raise_exc=None, raise_after=0):
        self._deltas, self._final = deltas, final
        self._raise_exc, self._raise_after = raise_exc, raise_after

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        for i, d in enumerate(self._deltas):
            if self._raise_exc is not None and i == self._raise_after:
                raise self._raise_exc
            yield d
        if self._raise_exc is not None and self._raise_after >= len(self._deltas):
            raise self._raise_exc

    def get_final_message(self):
        return self._final


def test_complete_retries_on_529_then_succeeds():
    fake = MagicMock()
    fake.messages.create.side_effect = [_status_error(529), _resp("recovered")]
    p = AnthropicProvider(api_key="sk-test", max_retries=3)
    with patch("ronin_agent_patterns.providers.anthropic_provider.anthropic.Anthropic", return_value=fake), \
         patch("time.sleep"):
        out = p.complete(system="s", messages=[Message(role="user", content="hi")], tools=[])
    assert out.text == "recovered"
    assert fake.messages.create.call_count == 2


def test_complete_reraises_after_max_retries():
    fake = MagicMock()
    fake.messages.create.side_effect = _status_error(529)
    p = AnthropicProvider(api_key="sk-test", max_retries=2)
    with patch("ronin_agent_patterns.providers.anthropic_provider.anthropic.Anthropic", return_value=fake), \
         patch("time.sleep"):
        with pytest.raises(anthropic.APIStatusError):
            p.complete(system="s", messages=[Message(role="user", content="hi")], tools=[])
    assert fake.messages.create.call_count == 3  # 1 + 2 retries


def test_stream_resets_and_recovers_after_midstream_drop():
    fake = MagicMock()
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    drop = anthropic.APIConnectionError(message="conn reset", request=req)
    fake.messages.stream.side_effect = [
        _FakeStream(["par", "tial"], _resp(), raise_exc=drop, raise_after=1),  # emits 'par' then drops
        _FakeStream(["full ", "answer"], _resp("full answer")),
    ]
    p = AnthropicProvider(api_key="sk-test", max_retries=3)
    with patch("ronin_agent_patterns.providers.anthropic_provider.anthropic.Anthropic", return_value=fake), \
         patch("time.sleep"):
        events = list(p.stream(system="s", messages=[Message(role="user", content="hi")], tools=[]))
    kinds = [e.type for e in events]
    assert "reset" in kinds, kinds          # partial was discarded
    assert kinds[-1] == "done"              # recovered on retry
    assert fake.messages.stream.call_count == 2


class _EmitThenFail(LLMProvider):
    def complete(self, **k): raise RuntimeError("no")
    def stream(self, **k):
        yield StreamEvent(type="text", text="A-partial")
        raise RuntimeError("provider A died mid-stream")


class _Good(LLMProvider):
    def complete(self, **k): return LLMResponse(text="B", stop_reason="end_turn")
    def stream(self, **k):
        yield StreamEvent(type="text", text="B-answer")
        yield StreamEvent(type="done", response=LLMResponse(text="B-answer", stop_reason="end_turn"))


def test_failover_resets_then_fails_over_after_partial():
    fo = FailoverProvider(providers=[_EmitThenFail(model="a"), _Good(model="b")])
    events = list(fo.stream(system="s", messages=[Message(role="user", content="hi")], tools=[]))
    kinds = [e.type for e in events]
    assert "reset" in kinds                 # A's partial discarded
    assert any(e.type == "text" and e.text == "B-answer" for e in events)  # B took over
    assert kinds[-1] == "done"
