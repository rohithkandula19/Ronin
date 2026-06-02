"""Tests for per-tool-result truncation — no single result can blow the window."""
from __future__ import annotations

from ronin_agent_patterns import FakeProvider, LLMResponse, ReActAgent, Tool, ToolCall


def test_cap_result_truncates_large() -> None:
    agent = ReActAgent(system="s", max_tool_result_chars=100)
    out = agent._cap_result("x" * 500)
    assert len(out) < 500
    assert "truncated" in out
    assert "500" in out  # reports the original size


def test_cap_result_leaves_small_untouched() -> None:
    agent = ReActAgent(system="s", max_tool_result_chars=100)
    assert agent._cap_result("short") == "short"


def test_cap_disabled_when_zero() -> None:
    agent = ReActAgent(system="s", max_tool_result_chars=0)
    big = "y" * 10000
    assert agent._cap_result(big) == big


def test_huge_tool_result_capped_in_run() -> None:
    """End-to-end: a tool returning a megabyte enters context truncated."""
    big_tool = Tool(
        name="dump", description="returns a lot",
        input_schema={"type": "object", "properties": {}},
        handler=lambda: "Z" * 50000,
    )
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id="1", name="dump", arguments={})]),
        LLMResponse(text="done"),
    ])
    agent = ReActAgent(system="s", tools=[big_tool], provider=provider,
                       max_tool_result_chars=2000)
    result = agent.run("dump it")
    assert result.success
    # the tool_result step recorded the capped (not 50k) payload
    tool_results = [s for s in result.trace if s.kind == "tool_result"]
    assert tool_results
    assert len(tool_results[0].content["result"]) < 5000
    assert "truncated" in tool_results[0].content["result"]
