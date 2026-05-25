from __future__ import annotations

from conftest import make_block, make_response  # type: ignore

from ro_claude_kit_agent_patterns import FakeProvider, LLMResponse, ReActAgent, Tool, ToolCall


def _big_result_tool() -> Tool:
    def handler() -> str:
        return "X" * 5000  # a fat tool result (e.g. a big file dump)

    return Tool(
        name="dump",
        description="returns a big blob",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


def test_compaction_truncates_old_tool_results() -> None:
    """After several fat tool results, compaction should shrink the old ones."""
    # Provider: call the tool 3 times, then answer.
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id="t1", name="dump", arguments={})], stop_reason="tool_use", usage={"input_tokens": 5, "output_tokens": 1}),
        LLMResponse(text="", tool_calls=[ToolCall(id="t2", name="dump", arguments={})], stop_reason="tool_use", usage={"input_tokens": 5, "output_tokens": 1}),
        LLMResponse(text="", tool_calls=[ToolCall(id="t3", name="dump", arguments={})], stop_reason="tool_use", usage={"input_tokens": 5, "output_tokens": 1}),
        LLMResponse(text="done", stop_reason="end_turn", usage={"input_tokens": 5, "output_tokens": 1}),
    ])
    agent = ReActAgent(
        system="x",
        tools=[_big_result_tool()],
        provider=provider,
        max_iterations=6,
        compact_after_tokens=1000,   # 3×5000 chars ≈ 3750 tokens >> 1000 → triggers
        compact_keep_recent=2,
    )
    result = agent.run("dump three times")
    assert result.success
    # The compaction note should appear in the trace at least once.
    assert any(s.kind == "thought" and "context compacted" in str(s.content) for s in result.trace)


def test_compaction_disabled_by_default() -> None:
    """Without compact_after_tokens, no compaction note appears."""
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id="t1", name="dump", arguments={})], stop_reason="tool_use", usage={"input_tokens": 5, "output_tokens": 1}),
        LLMResponse(text="done", stop_reason="end_turn", usage={"input_tokens": 5, "output_tokens": 1}),
    ])
    agent = ReActAgent(system="x", tools=[_big_result_tool()], provider=provider, max_iterations=4)
    result = agent.run("dump once")
    assert result.success
    assert not any("context compacted" in str(s.content) for s in result.trace)


def test_compaction_under_threshold_is_noop() -> None:
    """Small histories under the threshold aren't compacted."""
    provider = FakeProvider(responses=[
        LLMResponse(text="hi", stop_reason="end_turn", usage={"input_tokens": 5, "output_tokens": 1}),
    ])
    agent = ReActAgent(system="x", provider=provider, max_iterations=3, compact_after_tokens=100_000)
    result = agent.run("just answer")
    assert result.success
    assert not any("context compacted" in str(s.content) for s in result.trace)
