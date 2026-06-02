from __future__ import annotations

from ronin_agent_patterns import (
    FakeProvider,
    LLMResponse,
    SubAgent,
    SupervisorAgent,
    ToolCall,
)


def test_supervisor_delegates_and_synthesizes() -> None:
    """Orchestrator delegates to a sub-agent, then returns a synthesized answer."""
    provider = FakeProvider(responses=[
        # Orchestrator delegates
        LLMResponse(
            text="",
            tool_calls=[ToolCall(id="t1", name="delegate_to_researcher", arguments={"query": "what is 2+2?"})],
            stop_reason="tool_use",
        ),
        # Sub-agent answers
        LLMResponse(text="four", stop_reason="end_turn"),
        # Orchestrator synthesizes
        LLMResponse(text="The researcher says four.", stop_reason="end_turn"),
    ])
    researcher = SubAgent(name="researcher", description="finds facts", system="research stuff")
    agent = SupervisorAgent(system="orchestrate", sub_agents=[researcher], provider=provider)
    result = agent.run("ask the researcher what 2+2 is")

    assert result.success
    assert "four" in result.output.lower()
    sub_results = [s for s in result.trace if s.kind == "tool_result" and "sub_agent" in str(s.content)]
    assert sub_results
