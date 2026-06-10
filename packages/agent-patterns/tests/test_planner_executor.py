from __future__ import annotations

from ronin_agent_patterns import FakeProvider, LLMResponse, PlannerExecutorAgent


def test_planner_executor_happy_path() -> None:
    plan_text = (
        "Here is the plan:\n"
        "<plan>\n"
        '{"goal": "greet user", "steps": ["say hello"]}\n'
        "</plan>"
    )
    provider = FakeProvider(responses=[
        LLMResponse(text=plan_text, stop_reason="end_turn"),
        LLMResponse(text="hello!", stop_reason="end_turn"),
    ])
    agent = PlannerExecutorAgent(
        planner_system="plan",
        executor_system="execute",
        provider=provider,
    )
    result = agent.run("greet the user")

    assert result.success
    plan_steps = [s for s in result.trace if s.kind == "plan"]
    assert plan_steps and plan_steps[0].content["goal"] == "greet user"


def test_planner_executor_missing_plan_tags_fails_clearly() -> None:
    provider = FakeProvider(responses=[
        LLMResponse(text="here you go", stop_reason="end_turn"),
    ])
    agent = PlannerExecutorAgent(
        planner_system="plan",
        executor_system="exec",
        max_replans=0,
        provider=provider,
    )
    result = agent.run("do something")

    assert not result.success
    assert "plan" in (result.error or "").lower()
