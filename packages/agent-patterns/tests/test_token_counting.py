from __future__ import annotations

import pytest

from ronin_agent_patterns import Message, Tool, estimate_request_tokens, estimate_text_tokens


@pytest.mark.parametrize(
    "text",
    [
        "",
        "a",
        "plain ASCII text",
        "line one\nline two\nline three",
        "punctuation: []{}()<>!?",
        "cafe",
        "naive resume",
        "日本語の文章です",
        "emoji: ***",
        "mixed English 日本語 emoji",
        "x" * 100,
        "x" * 1_000,
    ],
)
def test_utf8_estimate_is_explicit_and_nonnegative(text: str) -> None:
    count = estimate_text_tokens(text)
    assert count.kind == "estimated"
    assert count.tokens >= 0
    assert "utf8" in count.method


@pytest.mark.parametrize("messages", [0, 1, 2, 4])
@pytest.mark.parametrize("tools", [0, 1, 3])
def test_request_estimate_accounts_for_message_and_tool_envelopes(messages: int, tools: int) -> None:
    history = [Message(role="user", content=f"message {index}") for index in range(messages)]
    definitions = [
        Tool(
            name=f"tool_{index}", description="a test tool",
            input_schema={"type": "object", "properties": {"value": {"type": "string"}}},
            handler=lambda: None,
        )
        for index in range(tools)
    ]
    count = estimate_request_tokens(system="policy", messages=history, tools=definitions)
    assert count.kind == "estimated"
    assert count.tokens >= estimate_text_tokens("policy").tokens


def test_request_estimate_includes_tool_call_arguments() -> None:
    small = estimate_request_tokens(
        system="s",
        messages=[Message(role="assistant", tool_calls=[])],
        tools=[],
    )
    large = estimate_request_tokens(
        system="s",
        messages=[Message(
            role="assistant",
            tool_calls=[{"id": "call", "name": "write", "arguments": {"content": "z" * 10_000}}],
        )],
        tools=[],
    )
    assert large.tokens > small.tokens


def test_request_estimate_is_deterministic() -> None:
    messages = [Message(role="user", content="same request")]
    first = estimate_request_tokens(system="s", messages=messages, tools=[])
    second = estimate_request_tokens(system="s", messages=messages, tools=[])
    assert first == second


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object"},
        {"type": "object", "properties": {"name": {"type": "string"}}},
        {"type": "object", "properties": {"count": {"type": "integer"}}},
        {"type": "object", "properties": {"enabled": {"type": "boolean"}}},
        {"type": "object", "properties": {"tags": {"type": "array", "items": {"type": "string"}}}},
        {"type": "object", "properties": {"nested": {"type": "object", "properties": {"value": {"type": "string"}}}}},
    ],
)
def test_request_estimate_accepts_json_tool_schemas(schema: dict) -> None:
    tool = Tool(name="schema_tool", description="schema under test", input_schema=schema, handler=lambda: None)
    count = estimate_request_tokens(system="s", messages=[], tools=[tool])
    assert count.kind == "estimated"
    assert count.tokens > 0
