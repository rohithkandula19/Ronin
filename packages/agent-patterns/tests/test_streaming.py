from __future__ import annotations

from ronin_agent_patterns import (
    FakeProvider,
    LLMResponse,
    ReActAgent,
    StreamEvent,
    Tool,
    ToolCall,
)


def test_fake_provider_stream_yields_text_then_done() -> None:
    """The provider stream contract: text deltas, then a terminal done event."""
    provider = FakeProvider(responses=[
        LLMResponse(text="hello there world", stop_reason="end_turn",
                    usage={"input_tokens": 3, "output_tokens": 3}),
    ])
    events = list(provider.stream(system="s", messages=[], tools=[]))
    assert events[-1].type == "done"
    assert events[-1].response is not None
    text_events = [e for e in events if e.type == "text"]
    assert len(text_events) == 3  # word-by-word
    assert "".join(e.text for e in text_events) == "hello there world"


def test_on_text_receives_streamed_answer() -> None:
    """run(on_text=...) drives the provider in streaming mode and forwards deltas."""
    provider = FakeProvider(responses=[
        LLMResponse(text="the answer is 42", stop_reason="end_turn",
                    usage={"input_tokens": 5, "output_tokens": 4}),
    ])
    agent = ReActAgent(system="x", provider=provider, max_iterations=3)

    chunks: list[str] = []
    result = agent.run("question?", on_text=chunks.append)

    assert result.success
    assert result.output == "the answer is 42"
    assert len(chunks) >= 2                       # arrived as multiple deltas
    assert "".join(chunks) == "the answer is 42"  # deltas reassemble to the answer


def test_streaming_still_drives_the_tool_loop() -> None:
    """Streaming mode must still execute tools and continue the ReAct loop."""
    calls: list[dict] = []

    def echo(value: str) -> str:
        calls.append({"value": value})
        return f"echoed:{value}"

    tool = Tool(
        name="echo",
        description="echo a value",
        input_schema={"type": "object", "properties": {"value": {"type": "string"}},
                      "required": ["value"]},
        handler=echo,
    )
    provider = FakeProvider(responses=[
        LLMResponse(text="let me use the tool",
                    tool_calls=[ToolCall(id="t1", name="echo", arguments={"value": "hi"})],
                    stop_reason="tool_use", usage={"input_tokens": 5, "output_tokens": 2}),
        LLMResponse(text="done: echoed hi", stop_reason="end_turn",
                    usage={"input_tokens": 5, "output_tokens": 3}),
    ])
    agent = ReActAgent(system="x", tools=[tool], provider=provider, max_iterations=4)

    chunks: list[str] = []
    result = agent.run("use echo", on_text=chunks.append)

    assert result.success
    assert calls == [{"value": "hi"}]
    assert "done" in result.output.lower()
    # text from BOTH turns streamed through
    joined = "".join(chunks)
    assert "let me use the tool" in joined and "done" in joined


def test_no_on_text_uses_complete_not_stream() -> None:
    """Without on_text, the agent uses the blocking complete() path (back-compat)."""
    provider = FakeProvider(responses=[
        LLMResponse(text="plain", stop_reason="end_turn", usage={}),
    ])
    agent = ReActAgent(system="x", provider=provider, max_iterations=2)
    result = agent.run("hi")  # no on_text
    assert result.success and result.output == "plain"
    assert len(provider.calls) == 1
