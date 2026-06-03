"""Proof the OpenAI-compat provider recovers tool calls emitted as text content
(the gpt-oss / open-model case where it 'just chats')."""
from __future__ import annotations

import httpx

from ronin_agent_patterns.providers.openai_compat import OpenAICompatProvider
from ronin_agent_patterns.types import Tool

_TOOL = Tool(name="write_file", description="write a file",
             input_schema={"type": "object", "properties": {}}, handler=lambda **k: "ok")


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _Client:
    def __init__(self, payload):
        self._p = payload

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, *a, **k):
        return _Resp(self._p)


def _patch(monkeypatch, content: str, tool_calls=None) -> None:
    payload = {"choices": [{"message": {"content": content, "tool_calls": tool_calls},
                            "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 5, "completion_tokens": 3}}
    monkeypatch.setattr(httpx, "Client", _Client(payload))


def test_recovers_text_tool_call(monkeypatch) -> None:
    # the model 'chats' a tool call into content with NO structured tool_calls
    _patch(monkeypatch, '<tool_call>{"name": "write_file", "arguments": {"path": "a.py"}}</tool_call>')
    prov = OpenAICompatProvider(model="gpt-oss-120b", api_key="x")
    resp = prov.complete(system="s", messages=[], tools=[_TOOL])
    assert resp.tool_calls, "should have recovered the text tool call"
    assert resp.tool_calls[0].name == "write_file"
    assert resp.tool_calls[0].arguments == {"path": "a.py"}
    assert resp.stop_reason == "tool_use"     # so the agent acts, not finalizes


def test_plain_answer_stays_an_answer(monkeypatch) -> None:
    _patch(monkeypatch, "The auth module validates the token, then…")
    prov = OpenAICompatProvider(model="gpt-oss-120b", api_key="x")
    resp = prov.complete(system="s", messages=[], tools=[_TOOL])
    assert resp.tool_calls == []
    assert resp.stop_reason == "end_turn"


def test_structured_calls_take_priority(monkeypatch) -> None:
    # if the provider already gave structured tool_calls, don't double-parse text
    structured = [{"id": "c1", "type": "function",
                   "function": {"name": "write_file", "arguments": '{"path": "real.py"}'}}]
    _patch(monkeypatch, "calling it", tool_calls=structured)
    prov = OpenAICompatProvider(model="gpt-oss-120b", api_key="x")
    resp = prov.complete(system="s", messages=[], tools=[_TOOL])
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].arguments == {"path": "real.py"}
    assert resp.tool_calls[0].id == "c1"      # the real structured id, not synthetic
