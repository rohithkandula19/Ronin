"""Regression for F2: a capped turn must preserve pairing-valid structured
history so the caller can adopt it (the next turn keeps every file read instead
of starting cold)."""
from __future__ import annotations

from ronin_agent_patterns import FakeProvider, LLMResponse, ReActAgent, Tool, ToolCall, Message
from ronin_agent_patterns.react import trim_to_complete_pairs


def _read_tool() -> Tool:
    return Tool(name="read", description="read a file",
                input_schema={"type": "object", "properties": {}},
                handler=lambda: "FILE CONTENTS ABC")


def test_caphit_preserves_pairing_valid_history():
    # Provider never stops → always asks for the tool → hits the cap.
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id=f"t{i}", name="read", arguments={})],
                    stop_reason="tool_use", usage={"input_tokens": 5, "output_tokens": 1})
        for i in range(10)
    ])
    agent = ReActAgent(system="x", tools=[_read_tool()], provider=provider, max_iterations=3)
    result = agent.run("read the file")
    assert result.success is False
    assert result.messages, "cap-hit must return the structured history"
    # Pairing valid: no trailing assistant-with-tool_calls left dangling.
    last = result.messages[-1]
    if getattr(last, "role", None) == "assistant" and getattr(last, "tool_calls", None):
        raise AssertionError("history ends on a dangling tool-call round")
    # The file the agent read is still in the adopted history.
    assert any("FILE CONTENTS ABC" in (getattr(m, "content", "") or "") for m in result.messages)


def test_trim_drops_dangling_assistant():
    msgs = [
        Message(role="user", content="go"),
        Message(role="assistant", content="", tool_calls=[ToolCall(id="a", name="read", arguments={})]),
        Message(role="tool", content="ok", tool_call_id="a"),
        # dangling: requested b, no tool result for b
        Message(role="assistant", content="", tool_calls=[ToolCall(id="b", name="read", arguments={})]),
    ]
    trimmed = trim_to_complete_pairs(msgs)
    assert len(trimmed) == 3
    assert trimmed[-1].role == "tool"


def test_trim_keeps_complete_pairs():
    msgs = [
        Message(role="assistant", content="", tool_calls=[ToolCall(id="a", name="read", arguments={})]),
        Message(role="tool", content="ok", tool_call_id="a"),
    ]
    assert trim_to_complete_pairs(msgs) == msgs


def test_interrupt_preserves_pairing_valid_history():
    """F2 (interrupt half): Ctrl-C mid-turn returns the partial history instead
    of raising, so the caller can adopt it."""
    calls = {"n": 0}

    def handler():
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KeyboardInterrupt  # simulate Ctrl-C during the 2nd tool run
        return "FILE CONTENTS ABC"

    tool = Tool(name="read", description="read",
                input_schema={"type": "object", "properties": {}}, handler=handler)
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id=f"t{i}", name="read", arguments={})],
                    stop_reason="tool_use", usage={"input_tokens": 5, "output_tokens": 1})
        for i in range(5)
    ])
    agent = ReActAgent(system="x", tools=[tool], provider=provider, max_iterations=5)
    result = agent.run("read it")  # must NOT raise
    assert result.success is False
    assert result.error == "interrupted by user"
    assert result.messages, "interrupt must preserve structured history"
    last = result.messages[-1]
    # trim drops the dangling assistant whose tool never ran
    assert not (getattr(last, "role", None) == "assistant" and getattr(last, "tool_calls", None))
    assert any("FILE CONTENTS ABC" in (getattr(m, "content", "") or "") for m in result.messages)
