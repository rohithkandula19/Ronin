from __future__ import annotations

from conftest import make_block, make_response  # type: ignore

from ronin_agent_patterns import FakeProvider, LLMResponse, ReActAgent, Tool, ToolCall


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


def test_est_tokens_counts_tool_call_arguments() -> None:
    """A persisted history whose bulk is in tool-call ARGUMENTS (write_file
    payloads) must trigger eviction — counting only m.content would miss it and
    let the history grow past the window unchecked."""
    from ronin_agent_patterns import Message

    # Build a long prior history where each turn's weight is a big write_file arg,
    # not tool-result content. 6 complete turn-groups.
    history: list[Message] = []
    for i in range(6):
        history.append(Message(role="user", content=f"turn {i}"))
        history.append(Message(
            role="assistant", content="",
            tool_calls=[ToolCall(id=f"w{i}", name="write_file",
                                 arguments={"path": f"f{i}.py", "content": "Z" * 4000})],
        ))
        history.append(Message(role="tool", tool_call_id=f"w{i}", name="write_file", content="ok"))
        history.append(Message(role="assistant", content="done"))

    provider = FakeProvider(responses=[LLMResponse(text="final", stop_reason="end_turn")])
    agent = ReActAgent(system="x", provider=provider, compact_after_tokens=2000, compact_keep_recent=4)
    result = agent.run("new turn", history=history)

    # The provider must have been handed an evicted (shorter) history — eviction
    # fired because tool-call args were counted. Without the fix the 24-msg history
    # would sail under a content-only estimate and never compact.
    sent = provider.calls[0]["messages"]
    user_turns = [m for m in sent if m["role"] == "user"]
    assert len(user_turns) < 7              # oldest groups evicted
    assert user_turns[-1]["content"] == "new turn"   # current turn always kept
    # never leaves an orphan tool result at the front
    assert sent[0]["role"] == "user"


def test_eviction_preserves_pairing_and_keeps_current_turn() -> None:
    """Even with a huge history, eviction must keep the list starting at a user
    message (no orphan tool results) and keep the most recent turn."""
    from ronin_agent_patterns import Message

    history: list[Message] = []
    for i in range(10):
        history.append(Message(role="user", content=f"q{i}"))
        history.append(Message(role="assistant", content="A" * 3000))

    provider = FakeProvider(responses=[LLMResponse(text="ok", stop_reason="end_turn")])
    agent = ReActAgent(system="x", provider=provider, compact_after_tokens=1500, compact_keep_recent=2)
    agent.run("current", history=history)

    sent = provider.calls[0]["messages"]
    assert sent[0]["role"] == "user"                 # valid conversation start
    assert sent[-1]["content"] == "current"          # current turn survives
