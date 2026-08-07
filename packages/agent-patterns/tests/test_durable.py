from __future__ import annotations

import pytest
from concurrent.futures import ThreadPoolExecutor

from ronin_agent_patterns import (
    BudgetLimits,
    DurableRunError,
    FakeProvider,
    LLMResponse,
    ReActAgent,
    RunBudget,
    RunJournal,
    Tool,
    ToolCall,
)


def test_journal_migrates_checkpoints_and_detects_corruption(tmp_path) -> None:
    journal = RunJournal(tmp_path / "runs.sqlite")
    run_id = journal.start({"phase": "inspect"}, run_id="run-inspect")
    checkpoint = journal.checkpoint(run_id, {"phase": "plan", "files": ["app.py"]})

    assert journal.interrupted_runs() == [run_id]
    assert journal.resume(run_id) == {"phase": "plan", "files": ["app.py"]}
    assert [event.kind for event in journal.events(run_id)] == [
        "run_started", "checkpoint_saved", "checkpoint_saved", "run_resumed",
    ]

    checkpoint.write_text("{}", encoding="utf-8")
    with pytest.raises(DurableRunError, match="integrity"):
        journal.resume(run_id)


def test_budget_warns_at_eighty_percent_and_blocks_the_next_tool() -> None:
    ticks = iter((0.0, 0.0, 0.0, 0.0))
    budget = RunBudget(BudgetLimits(max_tool_calls=2), clock=lambda: next(ticks))

    first = budget.begin_tool()
    budget.finish_tool()
    second = budget.begin_tool()
    budget.finish_tool()
    blocked = budget.begin_tool()

    assert first.allowed and not first.warnings
    assert second.allowed and any("tool calls budget warning" in warning for warning in second.warnings)
    assert not blocked.allowed
    assert "tool calls budget reached" in blocked.reason


def test_budget_reservations_are_atomic_across_parallel_tools() -> None:
    budget = RunBudget(BudgetLimits(max_concurrency=1))

    with ThreadPoolExecutor(max_workers=2) as workers:
        decisions = list(workers.map(lambda _: budget.begin_tool(), range(2)))

    assert sum(decision.allowed for decision in decisions) == 1
    budget.finish_tool()
    assert budget.begin_tool().allowed


def test_react_resumes_pending_tool_call_from_a_verified_checkpoint(tmp_path) -> None:
    attempts: list[str] = []

    def recoverable_tool() -> str:
        attempts.append("tool")
        if len(attempts) == 1:
            raise KeyboardInterrupt
        return "recovered"

    agent = ReActAgent(
        system="x",
        tools=[Tool(
            name="recoverable_tool",
            description="returns after a restart",
            input_schema={"type": "object", "properties": {}},
            handler=recoverable_tool,
        )],
        provider=FakeProvider(responses=[
            LLMResponse(
                text="I will use the tool.",
                tool_calls=[ToolCall(id="tool-1", name="recoverable_tool", arguments={})],
                stop_reason="tool_use",
            ),
            LLMResponse(text="The tool recovered.", stop_reason="end_turn"),
        ]),
    )
    journal = RunJournal(tmp_path / "runs.sqlite")

    interrupted = agent.run("recover", journal=journal, journal_run_id="run-recover")
    assert not interrupted.success
    assert journal.interrupted_runs() == ["run-recover"]

    resumed = agent.resume("run-recover", journal)

    assert resumed.success
    assert resumed.output == "The tool recovered."
    assert attempts == ["tool", "tool"]
    assert journal.interrupted_runs() == []
    assert [event.kind for event in journal.events("run-recover")].count("tool_started") == 2


def test_react_blocks_tool_before_execution_when_shared_budget_is_exhausted(tmp_path) -> None:
    calls: list[str] = []
    agent = ReActAgent(
        system="x",
        tools=[Tool(
            name="write_file",
            description="write",
            input_schema={"type": "object", "properties": {}},
            handler=lambda: calls.append("ran") or "wrote",
        )],
        provider=FakeProvider(responses=[
            LLMResponse(
                text="I will write.",
                tool_calls=[ToolCall(id="tool-1", name="write_file", arguments={})],
                stop_reason="tool_use",
                usage={"input_tokens": 1},
            ),
        ]),
    )
    journal = RunJournal(tmp_path / "runs.sqlite")
    budget = RunBudget(BudgetLimits(max_tokens=1))

    result = agent.run("write", journal=journal, journal_run_id="run-budget", budget=budget)

    assert not result.success
    assert "tokens budget reached" in (result.error or "")
    assert calls == []
    assert "run_finished" in [event.kind for event in journal.events("run-budget")]
