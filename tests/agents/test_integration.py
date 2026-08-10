"""The three pieces wired together the way a session wires them.

Real registry, real tools, real files in ``tmp_path``, real ``TaskTool``. The only
fakes are the model (its answer is a string constant) and, for the gate test, the
hook process — with one genuinely-executing shell hook to keep the argv path honest.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from agents_harness import context, seed, use

from ronin.agents.definitions import (
    BUILTIN_AGENTS,
    AgentDefinition,
    build_subagent_registry,
    load_agents,
    subagent_catalogue,
)
from ronin.agents.hooks import (
    HookConfig,
    HookEvent,
    HookRunner,
)
from ronin.agents.plan_mode import (
    approve,
    enter_plan_mode,
    objective_message,
    parse_plan,
)
from ronin.core.types import AgentState, Message, Mode, Role, Text, ToolResult, ToolUse
from ronin.tools.base import ToolContext
from ronin.tools.registry import ToolRegistry, build_registry
from ronin.tools.task import SubagentType

MODEL_PLAN = """\
Both handlers swallow the error, so a caller cannot tell "no result" from "it broke".

## Plan
1. Raise HandlerError from handler() in src/app.py
2. Add tests/test_handler.py covering the raise
"""

TEST_WRITER_FILE = """\
---
name: test-writer
description: Writes tests only. Cannot touch src.
tools: [read, glob, grep, write]
model: main
write_paths:
  - tests/**
---
You write tests under tests/ and nothing else.
"""


async def gated_execute(
    registry: ToolRegistry, runner: HookRunner, tool_use: ToolUse
) -> ToolResult:
    """What the orchestrator does: fire PreToolUse, block or run, then PostToolUse.

    Written here rather than in ``ronin.agents`` because the loop owns tool
    dispatch; this is the shape of the wiring the hooks module is designed for.
    """
    report = await runner.fire(
        HookEvent.PRE_TOOL_USE,
        tool_name=tool_use.name,
        data={"arguments": dict(tool_use.arguments)},
    )
    blocked = report.as_tool_result()
    if blocked is not None:
        return blocked
    result = await registry.execute(tool_use)
    await runner.fire(HookEvent.POST_TOOL_USE, tool_name=tool_use.name)
    return result


async def test_plan_mode_then_approval_then_a_write_that_the_plan_authorised(
    tmp_path: Path,
) -> None:
    seed(tmp_path)
    ctx = context(tmp_path)
    full = build_registry(ctx)

    setup = enter_plan_mode(full, names=("read", "glob", "grep"), ctx=context(tmp_path))
    assert setup.mode is Mode.PLAN
    refused = await setup.registry.execute(
        use("write", path="src/app.py", content="raise HandlerError\n")
    )
    assert not refused.ok
    assert (tmp_path / "src" / "app.py").read_bytes() == b"def handler():\n    return None\n"

    plan = parse_plan(MODEL_PLAN)
    decision = approve(plan)
    state = AgentState(
        messages=(Message(role=Role.USER, content_blocks=(Text("fix the handlers"),)),),
        cwd=str(tmp_path),
        mode=decision.mode,
    ).with_message(objective_message(plan))

    assert state.mode is Mode.AUTO_EDIT
    assert "STANDING OBJECTIVE" in state.messages[-1].text
    assert state.pairing_errors == ()

    await full.execute(use("read", path="src/app.py"))
    wrote = await full.execute(
        use("write", path="src/app.py", content="def handler():\n    raise HandlerError\n")
    )
    assert wrote.ok
    assert b"raise HandlerError" in (tmp_path / "src" / "app.py").read_bytes()


async def test_a_hook_blocks_a_write_before_it_reaches_the_filesystem(
    tmp_path: Path,
) -> None:
    """Exit 2 from a real shell hook, and the file is untouched afterwards."""
    seed(tmp_path)
    (tmp_path / "migrations").mkdir()
    ctx = context(tmp_path)
    registry = build_registry(ctx)
    runner = HookRunner(
        config=HookConfig.from_mapping(
            {
                "PreToolUse": [
                    {
                        "matcher": "write|edit|multi_edit",
                        "hooks": [
                            {
                                "command": (
                                    'if grep -q migrations; then echo "migrations are '
                                    'owned by the DBA" >&2; exit 2; fi'
                                ),
                                "name": "no-migrations",
                                "timeout": 10,
                            }
                        ],
                    }
                ]
            }
        ),
        cwd=str(tmp_path),
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
    )

    blocked = await gated_execute(
        registry,
        runner,
        use("write", path="migrations/003.sql", content="DROP TABLE users;\n"),
    )
    assert not blocked.ok
    assert "owned by the DBA" in blocked.error
    assert "no-migrations" in blocked.error
    assert not (tmp_path / "migrations" / "003.sql").exists()

    allowed = await gated_execute(
        registry, runner, use("write", path="tests/test_new.py", content="def test_x(): ...\n")
    )
    assert allowed.ok
    assert (tmp_path / "tests" / "test_new.py").exists()


async def test_a_subagent_defined_on_disk_runs_through_the_real_task_tool(
    tmp_path: Path,
) -> None:
    """A .md file becomes a type the shipped TaskTool dispatches to, lane enforced."""
    seed(tmp_path)
    agents_dir = tmp_path / ".ronin" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "test-writer.md").write_text(TEST_WRITER_FILE, encoding="utf-8")

    agents: Mapping[str, AgentDefinition] = load_agents(tmp_path)
    assert agents["test-writer"].source.endswith("test-writer.md")
    assert agents["test-writer"].tools == ("read", "glob", "grep", "write")

    base = build_registry(context(tmp_path))
    attempts: list[ToolResult] = []

    async def runner(prompt: str, subagent: SubagentType) -> str:
        # Stands in for `session.SubagentSession.run`: fresh context, narrowed
        # registry, and only the final message crosses back.
        child = ToolContext(root=tmp_path, env=base.ctx.env)
        lane = build_subagent_registry(base, agents[subagent.name], ctx=child)
        attempts.append(await lane.execute(use("write", path="src/app.py", content="# no\n")))
        attempts.append(
            await lane.execute(
                use("write", path="tests/test_handler.py", content="def test_h(): ...\n")
            )
        )
        assert child.read_files, "the child read set is its own"
        assert not base.ctx.read_files, "the parent's write guard must not be weakened"
        return f"wrote tests/test_handler.py; refused src/ ({prompt[:20]}…)"

    session_registry = build_registry(
        context(tmp_path),
        subagent_runner=runner,
        subagent_types=subagent_catalogue(dict(agents)),
    )
    result = await session_registry.execute(
        use("task", prompt="add a test for handler()", subagent_type="test-writer")
    )

    assert result.ok
    assert "wrote tests/test_handler.py" in result.content
    assert not attempts[0].ok and "tests/**" in attempts[0].error
    assert attempts[1].ok
    assert (tmp_path / "tests" / "test_handler.py").exists()
    assert (tmp_path / "src" / "app.py").read_bytes() == b"def handler():\n    return None\n"


async def test_the_task_tool_describes_the_types_it_actually_has(tmp_path: Path) -> None:
    async def runner(prompt: str, subagent: SubagentType) -> str:
        return "done"

    registry = build_registry(
        context(tmp_path),
        subagent_runner=runner,
        subagent_types=subagent_catalogue(dict(BUILTIN_AGENTS)),
    )
    spec = registry.get("task")
    assert spec is not None
    for name in BUILTIN_AGENTS:
        assert name in spec.description


def test_no_builtin_agent_can_spawn_another_subagent() -> None:
    """Depth-1 is a design limit in `session.FORBIDDEN_IN_SUBAGENTS`; do not fight it."""
    for definition in BUILTIN_AGENTS.values():
        assert "task" not in definition.tools
