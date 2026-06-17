"""Offline tests for the multi-agent orchestrator.

Every provider here is a ``FakeProvider`` returning canned responses, so the
whole orchestration — planning, sub-agent execution, synthesis — runs with NO
network and NO API keys. These prove the three properties the orchestrator
promises: a goal is decomposed into a plan, sub-agents run on their ASSIGNED
(mock) providers, and the results come back and are synthesized.
"""
from __future__ import annotations

import threading
import time

import pytest

from ronin_agent_patterns import (
    FakeProvider,
    LLMResponse,
    OrchestrationPlan,
    OrchestratorAgent,
    OrchestratorSubAgent,
    Subtask,
    Tool,
    ToolCall,
)
from ronin_agent_patterns.orchestrator import SubtaskResult


def _plan_response(json_text: str) -> LLMResponse:
    return LLMResponse(text=f"<plan>{json_text}</plan>", stop_reason="end_turn")


# --------------------------------------------------------------------------
# Pure scheduling (no LLM at all)
# --------------------------------------------------------------------------


def test_schedule_groups_independent_subtasks_into_one_wave() -> None:
    plan = OrchestrationPlan(
        goal="g",
        subtasks=[
            Subtask(id="a", description="A", assignee="r"),
            Subtask(id="b", description="B", assignee="r"),
        ],
    )
    waves = OrchestratorAgent.schedule(plan)
    assert len(waves) == 1
    assert {st.id for st in waves[0]} == {"a", "b"}


def test_schedule_orders_dependencies_into_separate_waves() -> None:
    plan = OrchestrationPlan(
        goal="g",
        subtasks=[
            Subtask(id="impl", description="build", assignee="dev", depends_on=["plan"]),
            Subtask(id="plan", description="design", assignee="arch"),
            Subtask(id="review", description="review", assignee="qa", depends_on=["impl"]),
        ],
    )
    waves = OrchestratorAgent.schedule(plan)
    assert [[st.id for st in w] for w in waves] == [["plan"], ["impl"], ["review"]]


def test_schedule_raises_on_cycle() -> None:
    plan = OrchestrationPlan(
        goal="g",
        subtasks=[
            Subtask(id="a", description="A", assignee="r", depends_on=["b"]),
            Subtask(id="b", description="B", assignee="r", depends_on=["a"]),
        ],
    )
    with pytest.raises(ValueError, match="cycle"):
        OrchestratorAgent.schedule(plan)


# --------------------------------------------------------------------------
# Full orchestration on mock providers
# --------------------------------------------------------------------------


def test_goal_is_decomposed_and_subagents_run_on_assigned_providers() -> None:
    """The core promise: a goal is decomposed into a plan, each subtask runs on
    its OWN assigned (mock) provider, and the results come back synthesized."""
    planner = FakeProvider(model="planner-model", responses=[
        _plan_response(
            '{"goal": "ship feature", "subtasks": ['
            '{"id": "research", "description": "find the API", "assignee": "researcher", "depends_on": []},'
            '{"id": "build", "description": "write the code", "assignee": "coder", "depends_on": ["research"]}'
            ']}'
        ),
        # second call on the planner provider = synthesis
        LLMResponse(text="Feature shipped: used the API and wrote the code.", stop_reason="end_turn"),
    ])
    # Each sub-agent has its OWN provider — different vendors/models.
    researcher_provider = FakeProvider(model="research-model", responses=[
        LLMResponse(text="The API is /v1/widgets.", stop_reason="end_turn"),
    ])
    coder_provider = FakeProvider(model="coder-model", responses=[
        LLMResponse(text="Wrote widgets.py calling /v1/widgets.", stop_reason="end_turn"),
    ])

    orch = OrchestratorAgent(
        provider=planner,
        sub_agents=[
            OrchestratorSubAgent(role="researcher", description="finds facts",
                                 system="research", provider=researcher_provider),
            OrchestratorSubAgent(role="coder", description="writes code",
                                 system="code", provider=coder_provider),
        ],
    )
    result = orch.run("ship the widgets feature")

    # 1. the goal was decomposed into a real plan
    assert result.plan is not None
    assert [st.id for st in result.plan.subtasks] == ["research", "build"]
    assert result.plan.subtasks[1].depends_on == ["research"]

    # 2. each sub-agent ran on ITS assigned provider (not the planner's)
    assert len(researcher_provider.calls) == 1
    assert len(coder_provider.calls) == 1
    # the researcher's provider was driven with the researcher's system prompt
    assert researcher_provider.calls[0]["system"] == "research"
    assert coder_provider.calls[0]["system"] == "code"
    # the planner provider was used twice: once to plan, once to synthesize
    assert len(planner.calls) == 2

    # 3. results came back and were synthesized
    assert result.success
    assert "shipped" in result.output.lower()
    assert {r.subtask_id for r in result.subtask_results} == {"research", "build"}
    research_res = next(r for r in result.subtask_results if r.subtask_id == "research")
    assert "v1/widgets" in research_res.output


def test_downstream_subtask_receives_upstream_output_as_context() -> None:
    """A dependent subtask is fed the completed upstream output, proving real
    hand-off (not just independent fan-out)."""
    planner = FakeProvider(responses=[
        _plan_response(
            '{"goal": "g", "subtasks": ['
            '{"id": "up", "description": "produce a token", "assignee": "a", "depends_on": []},'
            '{"id": "down", "description": "use the token", "assignee": "b", "depends_on": ["up"]}'
            ']}'
        ),
        LLMResponse(text="done", stop_reason="end_turn"),
    ])
    up_provider = FakeProvider(responses=[LLMResponse(text="TOKEN-42", stop_reason="end_turn")])
    down_provider = FakeProvider(responses=[LLMResponse(text="used it", stop_reason="end_turn")])

    orch = OrchestratorAgent(
        provider=planner,
        sub_agents=[
            OrchestratorSubAgent(role="a", description="up", system="s", provider=up_provider),
            OrchestratorSubAgent(role="b", description="down", system="s", provider=down_provider),
        ],
    )
    orch.run("g")

    # the downstream sub-agent's user message must contain the upstream output
    down_user_msg = down_provider.calls[0]["messages"][-1]["content"]
    assert "TOKEN-42" in down_user_msg


def test_independent_subtasks_run_in_parallel() -> None:
    """Two independent subtasks (no deps) execute concurrently, not serially.

    A ``Barrier(2)`` only releases once BOTH sub-agent threads reach it, so if the
    orchestrator ran them sequentially the barrier would time out and the test
    would fail. Passing proves real parallelism within a dependency wave."""
    planner = FakeProvider(responses=[
        _plan_response(
            '{"goal": "g", "subtasks": ['
            '{"id": "x", "description": "X", "assignee": "w", "depends_on": []},'
            '{"id": "y", "description": "Y", "assignee": "w", "depends_on": []}'
            ']}'
        ),
        LLMResponse(text="both done", stop_reason="end_turn"),
    ])
    barrier = threading.Barrier(2, timeout=5)

    class BarrierProvider(FakeProvider):
        def complete(self, **kwargs):  # type: ignore[override]
            barrier.wait()  # times out unless both subtasks are in flight at once
            time.sleep(0.01)
            return super().complete(**kwargs)

    worker = OrchestratorSubAgent(
        role="w", description="worker", system="s",
        provider=BarrierProvider(responses=[
            LLMResponse(text="x ok", stop_reason="end_turn"),
            LLMResponse(text="y ok", stop_reason="end_turn"),
        ]),
    )
    orch = OrchestratorAgent(provider=planner, sub_agents=[worker])
    result = orch.run("g")
    assert result.success
    assert len(result.subtask_results) == 2


def test_subagent_with_no_provider_falls_back_to_orchestrator_provider() -> None:
    """A sub-agent with provider=None runs on the orchestrator's default provider."""
    planner = FakeProvider(model="default", responses=[
        _plan_response(
            '{"goal": "g", "subtasks": ['
            '{"id": "only", "description": "do it", "assignee": "solo", "depends_on": []}'
            ']}'
        ),
        # the sub-agent runs on this same provider (next response), then synth
        LLMResponse(text="solo did the work", stop_reason="end_turn"),
        LLMResponse(text="final", stop_reason="end_turn"),
    ])
    orch = OrchestratorAgent(
        provider=planner,
        sub_agents=[OrchestratorSubAgent(role="solo", description="solo", system="s")],
    )
    result = orch.run("g")
    assert result.success
    # planner provider was called 3x: plan, sub-agent, synth
    assert len(planner.calls) == 3


def test_subagent_can_use_its_tools() -> None:
    """A sub-agent's assigned tool subset is wired into its ReAct sub-loop."""
    calls: list[str] = []

    def lookup(name: str) -> str:
        calls.append(name)
        return f"value-of-{name}"

    tool = Tool(
        name="lookup",
        description="look up a value",
        input_schema={"type": "object", "properties": {"name": {"type": "string"}},
                      "required": ["name"]},
        handler=lookup,
    )
    planner = FakeProvider(responses=[
        _plan_response(
            '{"goal": "g", "subtasks": ['
            '{"id": "t", "description": "look up foo", "assignee": "looker", "depends_on": []}'
            ']}'
        ),
        LLMResponse(text="synthesized", stop_reason="end_turn"),
    ])
    looker_provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="lookup", arguments={"name": "foo"})],
                    stop_reason="tool_use"),
        LLMResponse(text="The value is value-of-foo.", stop_reason="end_turn"),
    ])
    orch = OrchestratorAgent(
        provider=planner,
        sub_agents=[OrchestratorSubAgent(role="looker", description="looks up",
                                         system="s", tools=[tool], provider=looker_provider)],
    )
    result = orch.run("g")
    assert calls == ["foo"]  # the tool actually ran inside the sub-agent
    t_res = next(r for r in result.subtask_results if r.subtask_id == "t")
    assert "value-of-foo" in t_res.output


def test_failed_subtask_does_not_crash_and_marks_run_unsuccessful() -> None:
    planner = FakeProvider(responses=[
        _plan_response(
            '{"goal": "g", "subtasks": ['
            '{"id": "boom", "description": "explode", "assignee": "x", "depends_on": []}'
            ']}'
        ),
        LLMResponse(text="reported the failure", stop_reason="end_turn"),
    ])

    class CrashProvider(FakeProvider):
        def complete(self, **kwargs):  # type: ignore[override]
            raise RuntimeError("kaboom")

    orch = OrchestratorAgent(
        provider=planner,
        sub_agents=[OrchestratorSubAgent(role="x", description="x", system="s",
                                         provider=CrashProvider(responses=[]))],
    )
    result = orch.run("g")
    assert result.success is False
    assert result.subtask_results[0].success is False
    assert "kaboom" in (result.subtask_results[0].error or "")


def test_planner_rejects_unknown_assignee() -> None:
    planner = FakeProvider(responses=[
        _plan_response(
            '{"goal": "g", "subtasks": ['
            '{"id": "t", "description": "x", "assignee": "nobody", "depends_on": []}'
            ']}'
        ),
    ])
    orch = OrchestratorAgent(
        provider=planner,
        sub_agents=[OrchestratorSubAgent(role="real", description="r", system="s")],
    )
    result = orch.run("g")
    assert result.success is False
    assert "unknown role" in (result.error or "")


def test_synthesis_failure_falls_back_to_deterministic_summary() -> None:
    """If synthesis errors, the run still returns a useful summary of subtasks."""
    class PlanThenCrash(FakeProvider):
        def complete(self, **kwargs):  # type: ignore[override]
            # plan succeeds (first call), synth crashes (later call on same provider)
            if not self.calls:
                return super().complete(**kwargs)
            raise RuntimeError("synth down")

    planner = PlanThenCrash(responses=[
        _plan_response(
            '{"goal": "g", "subtasks": ['
            '{"id": "t", "description": "x", "assignee": "w", "depends_on": []}'
            ']}'
        ),
    ])
    worker_provider = FakeProvider(responses=[LLMResponse(text="worker output", stop_reason="end_turn")])
    orch = OrchestratorAgent(
        provider=planner,
        sub_agents=[OrchestratorSubAgent(role="w", description="w", system="s",
                                         provider=worker_provider)],
    )
    result = orch.run("g")
    # the subtask still succeeded and its output is in the fallback summary
    assert result.subtask_results[0].success
    assert "worker output" in result.output


def test_on_subtask_start_hook_fires_with_label() -> None:
    started: list[tuple[str, str]] = []
    planner = FakeProvider(responses=[
        _plan_response(
            '{"goal": "g", "subtasks": ['
            '{"id": "t", "description": "x", "assignee": "w", "depends_on": []}'
            ']}'
        ),
        LLMResponse(text="ok", stop_reason="end_turn"),
    ])
    worker_provider = FakeProvider(model="worker-7", responses=[
        LLMResponse(text="did x", stop_reason="end_turn"),
    ])
    orch = OrchestratorAgent(
        provider=planner,
        sub_agents=[OrchestratorSubAgent(role="w", description="w", system="s",
                                         provider=worker_provider)],
    )
    orch.run("g", on_subtask_start=lambda st, label: started.append((st.id, label)))
    assert started == [("t", "w@worker-7")]
