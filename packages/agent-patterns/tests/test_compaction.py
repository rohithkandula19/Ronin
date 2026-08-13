from __future__ import annotations

from conftest import make_block, make_response  # type: ignore

from ronin_agent_patterns import FakeProvider, LLMResponse, ReActAgent, TokenCount, Tool, ToolCall


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


def test_compaction_uses_provider_counter_and_records_final_count() -> None:
    class CountingProvider(FakeProvider):
        counts: int = 0

        def count_input_tokens(self, *, system, messages, tools):
            self.counts += 1
            return TokenCount(tokens=10, kind="native", method="test-counter")

    provider = CountingProvider(responses=[LLMResponse(text="done", stop_reason="end_turn")])
    result = ReActAgent(
        system="x", provider=provider, compact_after_tokens=100,
    ).run("answer")
    assert result.success
    assert provider.counts == 1
    assert result.usage["latest_context_tokens"] == 10


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


def test_compaction_writes_a_summary_marker_and_shrinks_history() -> None:
    """Folding old turns must (a) leave a single '[earlier context summarized: ...]'
    message, (b) shrink the history the provider is handed, and (c) still complete."""
    from ronin_agent_patterns import Message

    history: list[Message] = []
    for i in range(8):
        history.append(Message(role="user", content=f"please do step {i}"))
        history.append(Message(
            role="assistant", content="",
            tool_calls=[ToolCall(id=f"w{i}", name="write_file",
                                 arguments={"path": f"mod_{i}.py", "content": "Q" * 3000})],
        ))
        history.append(Message(role="tool", tool_call_id=f"w{i}", name="write_file", content="ok"))
        history.append(Message(role="assistant", content=f"finished step {i}"))

    provider = FakeProvider(responses=[LLMResponse(text="all done", stop_reason="end_turn")])
    agent = ReActAgent(system="x", provider=provider, compact_after_tokens=2000, compact_keep_recent=4)
    result = agent.run("wrap up", history=history)

    assert result.success
    assert result.output == "all done"

    sent = provider.calls[0]["messages"]
    # History shrank: 8*4 + 1 = 33 messages went in; far fewer come out.
    assert len(sent) < 33
    # Exactly one summary marker, at the front, carrying digest facts.
    summaries = [m for m in sent if str(m["content"]).startswith("[earlier context summarized:")]
    assert len(summaries) == 1
    assert sent[0]["content"].startswith("[earlier context summarized:")
    body = summaries[0]["content"]
    assert "files touched" in body and "mod_0.py" in body  # files survive the fold
    assert "write_file" in body                            # tools used survive
    # Current turn always kept intact at the tail.
    assert sent[-1]["content"] == "wrap up"
    # A summary message is a valid user-role conversation start.
    assert sent[0]["role"] == "user"


def test_repeated_compaction_stays_one_summary_and_keeps_task() -> None:
    """Across two compactions, an existing summary folds through (no nesting) and
    the original task's facts are carried forward, not lost."""
    from ronin_agent_patterns import ReActAgent as _RA

    agent = _RA(system="x", provider=FakeProvider(responses=[]),
                compact_after_tokens=500, compact_keep_recent=2)

    from ronin_agent_patterns import Message
    # First fold: a couple of old groups -> one summary.
    g1 = [
        [Message(role="user", content="ORIGINAL TASK build the parser"),
         Message(role="assistant", content="",
                 tool_calls=[ToolCall(id="a", name="write_file", arguments={"path": "parser.py"})]),
         Message(role="tool", tool_call_id="a", name="write_file", content="ok")],
    ]
    summary1 = agent._summarize_groups(g1)
    assert summary1.content.startswith("[earlier context summarized:")
    assert "parser.py" in summary1.content
    assert "build the parser" in summary1.content

    # Second fold: the prior summary plus a new group -> still ONE summary, and
    # the original task line carries through.
    g2 = [
        [summary1,
         Message(role="user", content="now add tests"),
         Message(role="assistant", content="",
                 tool_calls=[ToolCall(id="b", name="write_file", arguments={"path": "test_parser.py"})]),
         Message(role="tool", tool_call_id="b", name="write_file", content="ok")],
    ]
    summary2 = agent._summarize_groups(g2)
    # Not nested: the prefix appears exactly once.
    assert summary2.content.count("[earlier context summarized:") == 1
    # Both the carried original task and the newer work are present.
    assert "build the parser" in summary2.content
    assert "test_parser.py" in summary2.content


def test_short_run_history_is_untouched() -> None:
    """A short run under the threshold passes the history through verbatim, with
    no summary marker injected."""
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id="t1", name="dump", arguments={})], stop_reason="tool_use"),
        LLMResponse(text="done", stop_reason="end_turn"),
    ])
    agent = ReActAgent(system="x", tools=[_big_result_tool()], provider=provider,
                       max_iterations=4, compact_after_tokens=1_000_000, compact_keep_recent=2)
    result = agent.run("dump once")
    assert result.success
    # No summary marker anywhere in what the provider saw.
    for call in provider.calls:
        assert not any(str(m["content"]).startswith("[earlier context summarized:") for m in call["messages"])
    assert not any("context compacted" in str(s.content) for s in result.trace)
