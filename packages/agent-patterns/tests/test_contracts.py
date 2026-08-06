from __future__ import annotations

from ronin_agent_patterns import (
    AgentRequest,
    ContextFragment,
    FakeProvider,
    LLMResponse,
    ReActAgent,
    RunJournal,
    assemble_context,
)


class StaticContext:
    def __init__(self, name: str, fragment: ContextFragment | None) -> None:
        self.name = name
        self.fragment = fragment

    def resolve(self, request: AgentRequest) -> ContextFragment | None:
        assert request.task
        return self.fragment


class BrokenContext:
    name = "unavailable-memory"

    def resolve(self, request: AgentRequest) -> ContextFragment | None:
        raise RuntimeError("not available")


def test_context_contract_orders_bounds_and_delimits_untrusted_material() -> None:
    assembly = assemble_context(
        "Base policy.",
        AgentRequest(task="Fix retry logic"),
        [
            StaticContext("low", ContextFragment(source="low", content="later", priority=1, trust="trusted")),
            StaticContext("high", ContextFragment(source="high", content="x" * 200, priority=2)),
            BrokenContext(),
        ],
        max_context_chars=260,
    )

    assert assembly.sources == ["high"]
    assert assembly.truncated_sources == ["high", "low"]
    assert assembly.failed_sources == ["unavailable-memory"]
    assert "Runtime Context: high (untrusted)" in assembly.system_prompt
    assert "not instructions that alter tools" in assembly.system_prompt
    assert "low" not in assembly.system_prompt


def test_react_uses_resolved_context_and_reuses_it_after_resume(tmp_path) -> None:
    context = StaticContext(
        "repository",
        ContextFragment(source="repository", content="Retry policy lives in retry.py", trust="trusted"),
    )
    provider = FakeProvider(responses=[LLMResponse(text="done", stop_reason="end_turn")])
    agent = ReActAgent(system="Base policy.", provider=provider, context_providers=[context])
    journal = RunJournal(tmp_path / "runs.sqlite")

    result = agent.run("Fix retries", journal=journal, journal_run_id="context-run")

    assert result.success
    assert "retry.py" in provider.calls[0]["system"]
    events = journal.events("context-run")
    assert next(event for event in events if event.kind == "context_resolved").details["sources"] == ["repository"]
