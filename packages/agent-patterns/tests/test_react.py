from __future__ import annotations

import pytest

from ronin_agent_patterns import (
    FakeProvider,
    LLMResponse,
    ReActAgent,
    Tool,
    ToolCall,
)


def test_react_no_tools_returns_immediately() -> None:
    provider = FakeProvider(responses=[
        LLMResponse(text="Hello!", stop_reason="end_turn", usage={"input_tokens": 10, "output_tokens": 5}),
    ])
    result = ReActAgent(system="be helpful", provider=provider).run("hi")

    assert result.success
    assert "Hello" in result.output
    assert result.iterations == 1
    assert result.usage["input_tokens"] == 10


def test_react_calls_tool_then_finishes() -> None:
    calls: list[str] = []

    def calc(expression: str) -> str:
        calls.append(expression)
        return "42"

    tool = Tool(
        name="calc",
        description="evaluate math",
        input_schema={"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
        handler=calc,
    )

    provider = FakeProvider(responses=[
        LLMResponse(
            text="",
            tool_calls=[ToolCall(id="t1", name="calc", arguments={"expression": "6*7"})],
            stop_reason="tool_use",
        ),
        LLMResponse(text="The answer is 42.", stop_reason="end_turn"),
    ])
    result = ReActAgent(system="...", tools=[tool], provider=provider).run("what is 6*7?")

    assert result.success
    assert calls == ["6*7"]
    assert "42" in result.output
    kinds = [s.kind for s in result.trace]
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert "final" in kinds


def test_react_iteration_cap() -> None:
    tool = Tool(
        name="loop",
        description="loops",
        input_schema={"type": "object", "properties": {}},
        handler=lambda: "more",
    )
    # Endless tool-use loop
    provider = FakeProvider(responses=[
        LLMResponse(
            text="",
            tool_calls=[ToolCall(id=f"t{i}", name="loop", arguments={})],
            stop_reason="tool_use",
        )
        for i in range(10)
    ])
    result = ReActAgent(system="...", tools=[tool], provider=provider, max_iterations=3).run("loop forever")

    assert not result.success
    assert "max_iterations" in (result.error or "")
    assert result.iterations == 3


def test_react_unknown_tool_does_not_crash() -> None:
    provider = FakeProvider(responses=[
        LLMResponse(
            text="",
            tool_calls=[ToolCall(id="t1", name="ghost", arguments={})],
            stop_reason="tool_use",
        ),
        LLMResponse(text="ok done", stop_reason="end_turn"),
    ])
    result = ReActAgent(system="...", tools=[], provider=provider).run("call ghost")

    assert result.success
    errors = [s for s in result.trace if s.kind == "error"]
    assert any("ghost" in str(s.content) for s in errors)


def test_react_tool_exception_surfaces_as_error() -> None:
    def boom(**_: object) -> str:
        raise RuntimeError("kaboom")

    tool = Tool(
        name="boom",
        description="explodes",
        input_schema={"type": "object", "properties": {}},
        handler=boom,
    )
    provider = FakeProvider(responses=[
        LLMResponse(
            text="",
            tool_calls=[ToolCall(id="t1", name="boom", arguments={})],
            stop_reason="tool_use",
        ),
        LLMResponse(text="recovered", stop_reason="end_turn"),
    ])
    result = ReActAgent(system="...", tools=[tool], provider=provider).run("call boom")

    assert result.success
    tool_results = [s for s in result.trace if s.kind == "tool_result"]
    assert tool_results and tool_results[0].content["is_error"] is True
    assert "kaboom" in tool_results[0].content["result"]


def test_react_provider_receives_assistant_messages_with_tool_calls() -> None:
    """After a tool call, the next provider invocation should see the assistant's tool_calls
    and the tool's response message in the conversation history."""
    tool = Tool(
        name="echo",
        description="echo",
        input_schema={"type": "object", "properties": {"v": {"type": "string"}}, "required": ["v"]},
        handler=lambda v: v,
    )
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id="t1", name="echo", arguments={"v": "yo"})], stop_reason="tool_use"),
        LLMResponse(text="echoed", stop_reason="end_turn"),
    ])
    ReActAgent(system="...", tools=[tool], provider=provider).run("echo yo")

    assert len(provider.calls) == 2
    second_call_messages = provider.calls[1]["messages"]
    roles = [m["role"] for m in second_call_messages]
    assert roles == ["user", "assistant", "tool"]
    assert second_call_messages[1]["tool_calls"][0]["name"] == "echo"
    assert second_call_messages[2]["content"] == "yo"
