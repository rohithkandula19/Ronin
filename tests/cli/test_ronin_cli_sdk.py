"""The programmatic surface: it has to be small, honest, and closeable.

The tests that matter here are the promises: one agent is one conversation, the
exit code agrees with what ``ronin -p`` would have exited, ``aclose`` really
releases the runtime, and a workspace with no model configuration says so instead
of raising something a caller cannot act on.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import stream_harness as h

from ronin.cli.sdk import (
    ROUTER_CONFIG_ENV,
    Agent,
    AgentConfig,
    AgentResult,
    find_router_config,
    load_router,
)
from ronin.core.types import Budget, DangerLevel, Mode, ToolResult, ToolSpec, ToolUse
from ronin.providers.router import Role as ModelRole
from ronin.tools.base import MAX_RESULT_CHARS, Tool, ToolContext

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
    runtime = h.build_runtime(h.build_loaded(tmp_path), tools=h.ScriptedTools([h.reader()]))
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
    runtime = h.build_runtime(h.build_loaded(tmp_path), tools=h.ScriptedTools([h.writer(tmp_path)]))
    from ronin.cli.stream import Conversation

    model = h.ScriptedModel([h.call("edit", {"file_path": "a.py", "content": "1"}), h.say("done")])
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

    runtime = replace(h.build_runtime(h.build_loaded(tmp_path)), closers=(close_one, close_two))
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


# --------------------------------------------------------------------------- #
# the public surface: `from ronin import Agent`
# --------------------------------------------------------------------------- #


def test_every_promised_name_resolves() -> None:
    """`__all__` is a promise, and this is the check that it is kept. The names resolve
    lazily through a module `__getattr__`, so a moved or renamed export fails here rather
    than in the first script someone writes against the docs."""
    import ronin

    for name in ronin.__all__:
        assert getattr(ronin, name) is not None, name
    assert sorted(ronin.__all__) == sorted(dir(ronin)), "__all__ and __dir__ disagree"


def test_an_unknown_attribute_is_an_attribute_error() -> None:
    import ronin

    with pytest.raises(AttributeError, match="no attribute 'Nonexistent'"):
        ronin.Nonexistent  # noqa: B018


def test_importing_ronin_does_not_pull_in_the_application_layer() -> None:
    """`import ronin` must stay cheap. Reaching `Agent` costs the whole application layer
    — every provider adapter, the tool registry, the MCP client — and plenty of things
    import this package wanting none of that. Run in a subprocess because once a module
    is in `sys.modules` no in-process check can tell what put it there."""
    code = "import ronin, sys; print(any(m.startswith('ronin.cli') for m in sys.modules))"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False", "importing ronin eagerly loaded ronin.cli"


def test_the_documented_import_is_the_one_that_works() -> None:
    """The module docstring shows `from ronin import Agent, AgentConfig`. That exact line
    is the thing that failed before this change — the package root was a bare docstring."""
    from ronin import Agent as Exported
    from ronin import AgentConfig as ExportedConfig

    assert Exported is Agent
    assert ExportedConfig is AgentConfig


# --------------------------------------------------------------------------- #
# both call shapes, one implementation
# --------------------------------------------------------------------------- #


async def test_a_run_can_be_awaited_for_a_result(tmp_path: Path) -> None:
    agent, _model = agent_for(tmp_path, [h.say("the answer")])
    result = await agent.run("go")
    assert isinstance(result, AgentResult)
    assert result.text == "the answer"
    assert result.ok


async def test_the_same_run_can_be_iterated_for_typed_events(tmp_path: Path) -> None:
    """The spec's shape: `async for ev in Agent(config).run(prompt)`. Same method, same
    machinery — awaiting it folds exactly the stream iterating it yields."""
    agent, _model = agent_for(tmp_path, [h.say("the answer")])
    events: list[object] = []
    async for event in agent.run("go"):
        events.append(event)
    assert [type(event).__name__ for event in events] == ["TurnStart", "TextDelta", "TurnEnd"]


async def test_awaiting_and_iterating_see_the_same_stream(tmp_path: Path) -> None:
    """One implementation, checked rather than asserted in a comment: the events a caller
    iterates are the events the folded result carries."""
    agent, _model = agent_for(tmp_path, [h.say("first"), h.say("second")])

    streamed = [type(event).__name__ async for event in agent.run("one")]
    folded = await agent.run("two")

    assert streamed == [type(event).__name__ for event in folded.events]


async def test_stream_is_the_same_thing_spelled_differently(tmp_path: Path) -> None:
    agent, _model = agent_for(tmp_path, [h.say("hello")])
    events = [event async for event in agent.stream("go")]
    assert [type(event).__name__ for event in events] == ["TurnStart", "TextDelta", "TurnEnd"]


# --------------------------------------------------------------------------- #
# Agent(config): synchronous construction, deferred assembly
# --------------------------------------------------------------------------- #


def test_building_from_a_config_touches_nothing(tmp_path: Path) -> None:
    """`Agent(config)` has to be callable outside an event loop — that is the whole reason
    it exists — so it cannot read a file, resolve a router or connect to anything."""
    agent = Agent(AgentConfig(path=tmp_path, connect_mcp=False))
    with pytest.raises(RuntimeError, match="has not opened yet"):
        _ = agent.runtime


async def test_a_config_built_agent_opens_on_first_use(tmp_path: Path) -> None:
    """The deferred assembly. A stub router is injected rather than discovered, so this
    tests the opening and not the provider layer underneath it."""
    router, _stub = h.stub_router()
    config = AgentConfig(path=tmp_path, router=router, environ={}, record=False, connect_mcp=False)
    agent = Agent(config)
    async with agent:
        await agent.ready()
        assert agent.loaded.paths.workspace_root == tmp_path
        assert {spec.name for spec in agent.runtime.registry.specs()}, "it built a registry"


async def test_closing_a_config_built_agent_that_never_ran_is_not_an_error(
    tmp_path: Path,
) -> None:
    """There is nothing to close, and that is not a failure — a caller who builds an agent
    and then decides not to run it should not have to special-case the cleanup."""
    agent = Agent(AgentConfig(path=tmp_path))
    await agent.aclose()


# --------------------------------------------------------------------------- #
# custom tools
# --------------------------------------------------------------------------- #


class Stamp(Tool):
    """A caller's own tool: one required argument, and it records what it was given."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="stamp",
            description="Record a note. WHEN TO USE: when the caller asked for it.",
            danger_level=DangerLevel.READ_ONLY,
            json_schema={
                "type": "object",
                "properties": {"note": {"type": "string"}},
                "required": ["note"],
            },
        )

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        note = str(arguments.get("note", ""))
        self.calls.append(note)
        if note == "huge":
            # Deliberately over the per-result ceiling, so a test can watch the gate clamp
            # it. A tool is free to return this much; what the model sees is not.
            return ToolResult(ok=True, content="x" * (MAX_RESULT_CHARS + 1_000))
        return ToolResult(ok=True, content=f"stamped {note}")


async def opened_with(tmp_path: Path, *tools: Tool) -> Agent:
    router, _stub = h.stub_router()
    return await Agent.open(
        tmp_path, router=router, environ={}, record=False, connect_mcp=False, tools=list(tools)
    )


async def test_a_caller_can_register_a_tool_of_their_own(tmp_path: Path) -> None:
    """The gap this closes: before it, `Agent.open` had no way to add a tool, and
    `build_registry` took only named builtin dependencies. An embedder had to reassemble a
    Runtime by hand, reaching past the public surface into `ronin.cli.wire`."""
    stamp = Stamp()
    async with await opened_with(tmp_path, stamp) as agent:
        names = {spec.name for spec in agent.runtime.registry.specs()}
        assert "stamp" in names, "the caller's tool is not in the registry the model sees"
        assert "read" in names, "and the builtins are still there"

        result = await agent.runtime.registry.execute(
            ToolUse(id="c1", name="stamp", arguments={"note": "hello"})
        )

    assert result.ok, result.error
    assert stamp.calls == ["hello"], "the caller's tool did not actually run"


async def test_a_callers_tool_is_gated_like_a_builtin(tmp_path: Path) -> None:
    """The property worth asserting rather than assuming.

    A tool folded in *above* the gate would be the only thing in the process acting
    without hooks, taint tracking or an output budget. It goes in below, and the
    observable proof is the clamp: the tool returns more than the per-result ceiling and
    what comes back is bounded, which only the gate does.
    """
    stamp = Stamp()
    async with await opened_with(tmp_path, stamp) as agent:
        result = await agent.runtime.registry.execute(
            ToolUse(id="c2", name="stamp", arguments={"note": "huge"})
        )

    assert result.ok, result.error
    assert stamp.calls == ["huge"], "the tool ran"
    assert len(result.content) < MAX_RESULT_CHARS + 1_000, (
        "the caller's tool bypassed the gate's output budget"
    )


async def test_tools_from_a_config_reach_the_registry_too(tmp_path: Path) -> None:
    """`AgentConfig.tools` is the same door as `Agent.open(tools=...)`, so the two shapes
    cannot come to disagree about whether a caller's tools are honoured."""
    router, _stub = h.stub_router()
    stamp = Stamp()
    config = AgentConfig(
        path=tmp_path,
        router=router,
        environ={},
        record=False,
        connect_mcp=False,
        tools=[stamp],
    )
    async with Agent(config) as agent:
        await agent.ready()
        assert "stamp" in {spec.name for spec in agent.runtime.registry.specs()}


async def test_the_example_script_runs(tmp_path: Path) -> None:
    """`examples/sdk_quickstart.py` is the spec's "a script using the sdk completes a task
    and receives typed events", and it is run here rather than trusted.

    A subprocess on purpose: it is the only way to check what the *documented* import line
    actually gives a caller, and the script asserts its own invariants (the caller's tool
    reaching the registry, the folded result matching). It found a real gap while being
    written — `Tool`, `ToolContext` and `ToolSpec` were missing from the package root, so a
    caller could be told to bring their own tool and had no way to declare one.
    """
    script = Path(__file__).resolve().parents[2] / "examples" / "sdk_quickstart.py"
    assert script.is_file(), "the example the docs point at does not exist"
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert "the caller's tool did not reach the registry" not in result.stderr
    assert "exit code: 0" in result.stdout
