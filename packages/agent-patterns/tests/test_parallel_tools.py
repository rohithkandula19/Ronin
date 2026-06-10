"""Tests for parallel tool execution in the ReAct loop."""
from __future__ import annotations

import time

from ronin_agent_patterns.providers.base import ToolCall
from ronin_agent_patterns.providers.fake import FakeProvider
from ronin_agent_patterns.providers.base import LLMResponse
from ronin_agent_patterns.react import ReActAgent
from ronin_agent_patterns.types import Tool


def _slow_tool(name: str, delay: float = 0.2) -> Tool:
    def handler(**kwargs):
        time.sleep(delay)
        return f"{name} done"
    return Tool(name=name, description="", input_schema={"type": "object", "properties": {}},
                handler=handler)


def _provider_with_batch(names: list[str]) -> FakeProvider:
    calls = [ToolCall(id=f"c{i}", name=n, arguments={}) for i, n in enumerate(names)]
    return FakeProvider(responses=[
        LLMResponse(text="working", tool_calls=calls, stop_reason="tool_use", usage={}),
        LLMResponse(text="done", stop_reason="end_turn", usage={}),
    ])


def test_read_only_batch_runs_concurrently() -> None:
    names = ["read_a", "read_b", "read_c", "read_d"]
    tools = [_slow_tool(n, 0.2) for n in names]
    agent = ReActAgent(system="s", tools=tools, provider=_provider_with_batch(names))
    t0 = time.time()
    res = agent.run("read them", parallel_safe=lambda n: True)
    elapsed = time.time() - t0
    assert res.success
    # 4 × 0.2s sequential = 0.8s; concurrent should be well under 0.5s
    assert elapsed < 0.5, f"expected concurrent (<0.5s), took {elapsed:.2f}s"


def test_results_preserve_order() -> None:
    names = ["t0", "t1", "t2"]
    tools = [_slow_tool(n, 0.05) for n in names]
    agent = ReActAgent(system="s", tools=tools, provider=_provider_with_batch(names))
    res = agent.run("go", parallel_safe=lambda n: True)
    # tool result messages must follow the assistant turn, in original call order
    tool_msgs = [s for s in res.trace if s.kind == "tool_result"]
    assert [m.content["name"] for m in tool_msgs] == names


def test_no_parallel_when_not_safe() -> None:
    # if parallel_safe says no, it falls back to sequential (still correct)
    names = ["t0", "t1"]
    tools = [_slow_tool(n, 0.05) for n in names]
    agent = ReActAgent(system="s", tools=tools, provider=_provider_with_batch(names))
    res = agent.run("go", parallel_safe=lambda n: False)
    assert res.success
    assert len([s for s in res.trace if s.kind == "tool_result"]) == 2


def test_single_call_uses_sequential_path() -> None:
    agent = ReActAgent(system="s", tools=[_slow_tool("only", 0.01)],
                       provider=_provider_with_batch(["only"]))
    res = agent.run("go", parallel_safe=lambda n: True)
    assert res.success
