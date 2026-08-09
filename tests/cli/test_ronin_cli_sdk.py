"""The programmatic surface: it has to be small, honest, and closeable.

The tests that matter here are the promises: one agent is one conversation, the
exit code agrees with what ``ronin -p`` would have exited, ``aclose`` really
releases the runtime, and a workspace with no model configuration says so instead
of raising something a caller cannot act on.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import stream_harness as h

from ronin.cli.sdk import (
    ROUTER_CONFIG_ENV,
    Agent,
    AgentResult,
    find_router_config,
    load_router,
)
from ronin.core.types import Budget, Mode
from ronin.providers.router import Role as ModelRole

MODELS_TOML = """\
[models.local]
provider = "openai-compat"
model = "test-model"
base_url = "http://127.0.0.1:1/v1"

[roles]
main = "local"
"""


def agent_for(tmp_path: Path, responses: list[object]) -> tuple[Agent, h.ScriptedModel]:
    """An agent over a hand-built runtime — no wire, no router, no model."""
    runtime = h.build_runtime(
        h.build_loaded(tmp_path), tools=h.ScriptedTools([h.reader()])
    )
    model = h.ScriptedModel(responses)  # type: ignore[arg-type]
    from ronin.cli.stream import Conversation

    return Agent(runtime, conversation=Conversation(model=model)), model


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #


async def test_run_returns_the_answer_and_a_zero_exit_code(tmp_path: Path) -> None:
    agent, _model = agent_for(tmp_path, [h.say("the answer")])

    result = await agent.run("ask something")

    assert isinstance(result, AgentResult)
    assert result.text == "the answer"
    assert result.exit_code == 0
    assert result.ok
    assert result.stop_reason == "no_tool_calls"


async def test_one_agent_is_one_conversation(tmp_path: Path) -> None:
    agent, model = agent_for(tmp_path, [h.say("first"), h.say("second")])

    await agent.run("one")
    await agent.run("two")

    # The second call saw the first call's transcript. Without that, compaction,
    # --continue and the 200-turn recall property all mean nothing.
    assert len(model.calls[1].messages) > len(model.calls[0].messages)
    assert any("one" in m.text for m in model.calls[1].messages)


async def test_reset_forgets_the_conversation_and_keeps_the_runtime(
    tmp_path: Path,
) -> None:
    agent, model = agent_for(tmp_path, [h.say("first"), h.say("second")])
    runtime = agent.runtime

    await agent.run("one")
    agent.reset()
    await agent.run("two")

    assert agent.runtime is runtime
    assert len(model.calls[1].messages) == len(model.calls[0].messages)


async def test_stream_yields_the_same_events_run_folds(tmp_path: Path) -> None:
    agent, _model = agent_for(tmp_path, [h.say("streamed")])

    events = await h.collect(agent.stream("go"))

    assert [type(event).__name__ for event in events] == [
        "TurnStart",
        "TextDelta",
        "TurnEnd",
    ]


async def test_a_budget_ceiling_is_honoured(tmp_path: Path) -> None:
    agent, model = agent_for(tmp_path, [h.say("first", tokens=100), h.say("second")])

    first = await agent.run("one", budget=Budget(max_tokens=10))
    assert first.exit_code == 0
    second = await agent.run("two")

    assert second.stop_reason == "token_budget"
    assert model.turns == 1, "the second turn must not reach the model at all"


async def test_state_is_a_resumable_value(tmp_path: Path) -> None:
    agent, _model = agent_for(tmp_path, [h.say("answered")])

    await agent.run("go")

    state = agent.state
    assert state.messages
    assert state.pairing_errors == ()


async def test_notes_lift_the_conversation_degradations_into_workspace_notes(
    tmp_path: Path,
) -> None:
    runtime = h.build_runtime(
        h.build_loaded(tmp_path), tools=h.ScriptedTools([h.writer(tmp_path)])
    )
    from ronin.cli.stream import Conversation

    model = h.ScriptedModel(
        [h.call("edit", {"file_path": "a.py", "content": "1"}), h.say("done")]
    )
    agent = Agent(runtime, conversation=Conversation(model=model))

    await agent.run("edit", verify=False)

    assert any(note.subject == "session" for note in agent.notes)
    assert all(note.detail for note in agent.notes)


# --------------------------------------------------------------------------- #
# Lifetime
# --------------------------------------------------------------------------- #


async def test_aclose_is_idempotent_and_runs_every_closer(tmp_path: Path) -> None:
    closed: list[str] = []

    async def close_one() -> None:
        closed.append("one")

    async def close_two() -> None:
        closed.append("two")

    from dataclasses import replace

    runtime = replace(
        h.build_runtime(h.build_loaded(tmp_path)), closers=(close_one, close_two)
    )
    agent = Agent(runtime)

    await agent.aclose()
    await agent.aclose()

    # Reverse order, once each: a second aclose must not close a shell twice.
    assert closed == ["two", "one"]


async def test_the_async_context_manager_closes_on_the_way_out(tmp_path: Path) -> None:
    closed: list[str] = []

    async def close() -> None:
        closed.append("shell")

    from dataclasses import replace

    runtime = replace(h.build_runtime(h.build_loaded(tmp_path)), closers=(close,))

    async with Agent(runtime) as agent:
        assert agent.runtime is runtime
    assert closed == ["shell"]


async def test_a_closed_agent_refuses_to_run(tmp_path: Path) -> None:
    agent, _model = agent_for(tmp_path, [])
    await agent.aclose()

    with pytest.raises(RuntimeError, match="closed"):
        agent.stream("go")


# --------------------------------------------------------------------------- #
# Router discovery
# --------------------------------------------------------------------------- #


def test_no_model_configuration_is_a_named_failure_not_a_traceback(
    tmp_path: Path,
) -> None:
    loaded = h.build_loaded(tmp_path)

    with pytest.raises(FileNotFoundError) as caught:
        load_router(loaded.paths, environ={})

    message = str(caught.value)
    # It has to name every path it looked in and the env var, because "no models
    # configured" is the most common first-run failure there is.
    assert str(tmp_path.resolve() / ".ronin/models.toml") in message
    assert ROUTER_CONFIG_ENV in message
    assert "examples/models.toml" in message


def test_a_project_config_beats_the_user_one(tmp_path: Path) -> None:
    loaded = h.build_loaded(tmp_path)
    project = loaded.paths.workspace_root / ".ronin"
    project.mkdir(parents=True, exist_ok=True)
    (project / "models.toml").write_text(MODELS_TOML, encoding="utf-8")
    (loaded.paths.home / "models.toml").write_text(MODELS_TOML, encoding="utf-8")

    found = find_router_config(loaded.paths, environ={})

    assert found == project / "models.toml"


def test_the_environment_variable_wins_over_both(tmp_path: Path) -> None:
    loaded = h.build_loaded(tmp_path)
    explicit = tmp_path / "elsewhere.toml"
    explicit.write_text(MODELS_TOML, encoding="utf-8")
    project = loaded.paths.workspace_root / ".ronin"
    project.mkdir(parents=True, exist_ok=True)
    (project / "models.toml").write_text(MODELS_TOML, encoding="utf-8")

    found = find_router_config(loaded.paths, environ={ROUTER_CONFIG_ENV: str(explicit)})

    assert found == explicit
    router = load_router(loaded.paths, environ={ROUTER_CONFIG_ENV: str(explicit)})
    assert router.spec_for(ModelRole.MAIN).model == "test-model"


# --------------------------------------------------------------------------- #
# open()
# --------------------------------------------------------------------------- #


async def test_open_assembles_a_real_workspace_with_an_injected_router(
    tmp_path: Path,
) -> None:
    (tmp_path / "RONIN.md").write_text("# project\n\nbe careful\n", encoding="utf-8")
    router, _stub = h.stub_router()

    agent = await Agent.open(tmp_path, router=router, home=tmp_path / "home", record=False)
    try:
        assert agent.loaded.paths.workspace_root == tmp_path.resolve()
        assert agent.loaded.memory.files, "RONIN.md should have been loaded"
        assert "be careful" in agent.runtime.system
        assert agent.runtime.transcript is None
    finally:
        await agent.aclose()


async def test_open_in_plan_mode_hands_over_a_read_only_registry(tmp_path: Path) -> None:
    router, _stub = h.stub_router()

    agent = await Agent.open(
        tmp_path, router=router, mode=Mode.PLAN, home=tmp_path / "home", record=False
    )
    try:
        names = {spec.name for spec in agent.runtime.registry.specs()}
        assert names and not (names & {"write", "edit", "multi_edit", "bash"})
        assert all(spec.danger_level == 0 for spec in agent.runtime.registry.specs())
        assert not any(spec.requires_approval for spec in agent.runtime.registry.specs())
    finally:
        await agent.aclose()
