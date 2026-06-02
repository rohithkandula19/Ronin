from __future__ import annotations

from ronin_agent_patterns import FakeProvider, LLMResponse, ReflexionAgent


def test_reflexion_accepts_first_attempt() -> None:
    provider = FakeProvider(responses=[
        LLMResponse(text="answer one", stop_reason="end_turn"),
        LLMResponse(text="ACCEPTABLE: yes\nFEEDBACK: looks great", stop_reason="end_turn"),
    ])
    agent = ReflexionAgent(agent_system="do", critic_system="critique", provider=provider)
    result = agent.run("do the thing")

    assert result.success
    assert result.iterations == 1
    refl = [s for s in result.trace if s.kind == "reflection"]
    assert refl and refl[0].content["is_acceptable"] is True


def test_reflexion_retries_then_accepts() -> None:
    provider = FakeProvider(responses=[
        LLMResponse(text="weak answer", stop_reason="end_turn"),
        LLMResponse(text="ACCEPTABLE: no\nFEEDBACK: be more specific", stop_reason="end_turn"),
        LLMResponse(text="strong answer", stop_reason="end_turn"),
        LLMResponse(text="ACCEPTABLE: yes\nFEEDBACK: nice", stop_reason="end_turn"),
    ])
    agent = ReflexionAgent(agent_system="do", critic_system="critique", max_attempts=3, provider=provider)
    result = agent.run("do the thing")

    assert result.success
    assert result.iterations == 2
    assert "strong" in result.output


def test_reflexion_exhausts_attempts() -> None:
    # Always rejects
    rounds = [
        LLMResponse(text="weak", stop_reason="end_turn"),
        LLMResponse(text="ACCEPTABLE: no\nFEEDBACK: try again", stop_reason="end_turn"),
    ] * 3
    provider = FakeProvider(responses=rounds)
    agent = ReflexionAgent(agent_system="do", critic_system="critique", max_attempts=2, provider=provider)
    result = agent.run("do the thing")

    assert not result.success
    assert "max_attempts" in (result.error or "")
