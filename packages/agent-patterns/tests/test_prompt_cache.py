"""Tests for Anthropic prompt caching — system, tools, and conversation prefix."""
from __future__ import annotations

from ronin_agent_patterns.providers.anthropic_provider import AnthropicProvider, _mark_cached
from ronin_agent_patterns.providers.base import Message, ToolCall
from ronin_agent_patterns.types import Tool

_TOOL = Tool(name="read_file", description="read", input_schema={"type": "object", "properties": {}},
             handler=lambda **k: "")


def _provider(cache=True) -> AnthropicProvider:
    return AnthropicProvider(model="claude-sonnet-4-6", api_key="x", cache_prompt=cache)


def _last_block(msg) -> dict:
    c = msg["content"]
    return c[-1] if isinstance(c, list) else {"text": c}


def test_system_and_tools_cached() -> None:
    kw = _provider()._build_kwargs(system="SYS", messages=[Message(role="user", content="hi")],
                                   tools=[_TOOL], max_tokens=100)
    assert kw["system"][0].get("cache_control") == {"type": "ephemeral"}
    assert kw["tools"][-1].get("cache_control") == {"type": "ephemeral"}


def test_conversation_prefix_cached_multi_turn() -> None:
    msgs = [
        Message(role="user", content="fix the bug"),
        Message(role="assistant", content="", tool_calls=[ToolCall(id="1", name="read_file", arguments={})]),
        Message(role="tool", tool_call_id="1", name="read_file", content="file contents here"),
    ]
    kw = _provider()._build_kwargs(system="SYS", messages=msgs, tools=[_TOOL], max_tokens=100)
    # the last (tool result) message's block carries the cache breakpoint
    assert _last_block(kw["messages"][-1]).get("cache_control") == {"type": "ephemeral"}


def test_single_turn_no_message_breakpoint() -> None:
    kw = _provider()._build_kwargs(system="SYS", messages=[Message(role="user", content="hi")],
                                   tools=[_TOOL], max_tokens=100)
    # one short message → no message-level cache_control (system/tools still cached)
    last = kw["messages"][-1]
    assert "cache_control" not in str(last)


def test_cache_disabled() -> None:
    msgs = [Message(role="user", content="a"), Message(role="assistant", content="b")]
    kw = _provider(cache=False)._build_kwargs(system="SYS", messages=msgs, tools=[_TOOL], max_tokens=100)
    assert "cache_control" not in str(kw["messages"])
    assert kw["tools"][-1].get("cache_control") is None


def test_mark_cached_promotes_string() -> None:
    msg = {"role": "user", "content": "hello"}
    _mark_cached(msg)
    assert isinstance(msg["content"], list)
    assert msg["content"][0]["cache_control"] == {"type": "ephemeral"}
