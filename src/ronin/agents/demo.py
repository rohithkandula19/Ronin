"""See the modes-and-extensibility layer work: ``python -m ronin.agents.demo``.

Offline, in a throwaway temp directory, against the real registry and real tools.
Five things are demonstrated, and four of them are refusals — this layer is mostly
made of refusals, and a refusal is the half of the behaviour a passing test suite
does not show you.

The hook at the end runs a genuine ``/bin/sh`` one-liner. Local, no network, no API
key: the point is that argv construction and exit-code plumbing are real, because
those are exactly what a stubbed runner would paper over.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ..core.types import ToolResult, ToolUse
from ..tools.base import ToolContext
from ..tools.registry import ToolRegistry, build_registry
from ..tools.task import SubagentType
from .definitions import (
    BUILTIN_AGENTS,
    build_subagent_registry,
    load_agents,
    rank,
    select,
    subagent_catalogue,
)
from .hooks import HookConfig, HookEvent, HookRunner
from .plan_mode import (
    PLAN_MODE_TOOLS,
    approve,
    assert_read_only,
    enter_plan_mode,
    parse_plan,
)

MODEL_PLAN_ANSWER = """\
I read src/app.py and src/util.py: both handlers swallow the error and return None,
so a caller cannot tell "no result" from "it broke". **Rationale:** make the failure
visible at the one place that already knows about it.

## Plan
1. Add a `HandlerError` to src/errors.py with the offending path on it.
2. Raise it from `handler()` in src/app.py instead of returning None
   (the caller in src/util.py already has a try block).
3. Add tests/test_handler.py covering the raise and the existing happy path.

This keeps the change to three files and leaves the retry logic alone.
"""

WRITER_DEFINITION = """\
---
name: doc-writer
description: Updates documentation under docs/ when the change is prose, not code.
model: fast
tools: [read, glob, grep, write]
write_paths:
  - docs/**
max_iterations: 6
---
You are the doc-writer subagent. You write documentation and you can only write
under docs/. Match the voice of the files already there.
"""

BLOCKING_HOOK = (
    'grep -q migrations; then echo "migrations are owned by the DBA; '
    'open a ticket instead" >&2; exit 2; else exit 0; fi'
)


def rule(title: str) -> None:
    print(f"\n{'─' * 78}\n{title}\n{'─' * 78}")


def show(label: str, result: ToolResult) -> None:
    mark = "✔" if result.ok else "✗"
    body = (result.content if result.ok else result.error).split("\n")
    print(f"  {mark} {label}")
    for line in body[:6]:
        print(f"      {line}")
    if len(body) > 6:
        print(f"      … ({len(body) - 6} more lines)")


async def scripted_runner(prompt: str, subagent: SubagentType) -> str:
    """Stands in for the real nested loop, which lives in ``ronin.session``."""
    return f"[{subagent.name}] {prompt[:40]}… → src/app.py:2"


def seed(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.py").write_text("def handler():\n    return None\n")
    (root / "src" / "util.py").write_text("def helper():\n    return None\n")
    agents_dir = root / ".ronin" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "doc-writer.md").write_text(WRITER_DEFINITION, encoding="utf-8")


def use(name: str, **args: object) -> ToolUse:
    return ToolUse(id=f"call_{name}", name=name, arguments=args)


async def demo_plan_registry(base: ToolRegistry) -> None:
    rule("1. plan mode: the registry does not contain write, so write cannot happen")
    setup = enter_plan_mode(base)
    print(f"  full session tools : {[s.name for s in base.specs()]}")
    print(f"  plan mode tools    : {[s.name for s in setup.registry.specs()]}")
    print(f"  planning model role: {setup.model_role!r} (a name — agents/ cannot")
    print("                       import the router; the orchestrator resolves it)")
    print(f"  requested subset   : {list(PLAN_MODE_TOOLS)}")
    assert_read_only(setup.registry)
    print("  assert_read_only(registry) → ok: every spec is READ_ONLY\n")

    blocked = await setup.registry.execute(use("write", path="src/app.py", content="# no\n"))
    show("the model calls write anyway", blocked)

    print("\n  And widening it by hand fails loudly rather than quietly:")
    try:
        assert_read_only(base.subset(["read", "grep", "write"]))
    except Exception as exc:  # the demo exists to show the message
        print(f"      ✗ {type(exc).__name__}: {exc}")


def demo_plan_pinning() -> None:
    rule("2. the plan is parsed from prose, approved, and pinned as the objective")
    plan = parse_plan(MODEL_PLAN_ANSWER)
    print(f"  parsed {len(plan.steps)} steps from a numbered list under a heading:")
    for step in plan.steps:
        print(f"      {step.index}. {step.text}")
    print(f"  rationale: {plan.rationale[:70]}…\n")

    decision = approve(plan, note="looks right, go")
    print(f"  approved → session mode becomes {decision.mode.value!r}, leaving plan mode behind")
    print("  pinned into context every turn from here on:\n")
    for line in decision.objective.split("\n"):
        print(f"      │ {line}")

    print("\n  An unparseable answer is an error that keeps the raw text:")
    try:
        parse_plan("I had a look and honestly it seems fine to me.")
    except Exception as exc:  # the demo exists to show the message
        print(f"      ✗ {type(exc).__name__}: {exc}")
        print(f"        raw preserved: {getattr(exc, 'raw', '')!r}")


def demo_definitions(root: Path) -> None:
    rule("3. a subagent loaded from .ronin/agents/doc-writer.md on disk")
    agents = load_agents(root)
    loaded = agents["doc-writer"]
    print(f"  builtins: {sorted(BUILTIN_AGENTS)}")
    print(f"  + from file: {sorted(set(agents) - set(BUILTIN_AGENTS))}")
    print(f"  doc-writer  source={loaded.source}")
    print(f"              model={loaded.model!r}  tools={list(loaded.tools)}")
    print(
        f"              write_paths={list(loaded.write_paths)}  "
        f"max_iterations={loaded.max_iterations}"
    )
    print(f"              prompt starts: {loaded.system_prompt.splitlines()[0]!r}")
    catalogue = subagent_catalogue(agents)
    print(f"  catalogue handed to TaskTool: {sorted(catalogue)}")

    print("\n  Dispatch fallback (the model normally names the type itself):")
    for task in (
        "where is the retry backoff handled in this repository",
        "write a regression test for the CRLF bug",
        "order me a sandwich",
    ):
        chosen = select(task, catalogue)
        top = rank(task, catalogue)[:2]
        scores = ", ".join(f"{name}={score:.2f}" for name, score in top)
        print(f"      {task!r}\n        → {chosen.name if chosen else None}  [{scores}]")


async def demo_path_restriction(base: ToolRegistry, root: Path) -> None:
    rule("4. test-writer cannot touch src/ — enforced by the tool, not the prompt")
    definition = BUILTIN_AGENTS["test-writer"]
    registry = build_subagent_registry(
        base, definition, ctx=ToolContext(root=root, env={"PATH": "/usr/bin:/bin"})
    )
    print(f"  test-writer tools: {[s.name for s in registry.specs()]}")
    print(f"  write_paths      : {list(definition.write_paths)}\n")

    refused = await registry.execute(
        use("write", path="src/app.py", content="def handler():\n    return 1\n")
    )
    show("write src/app.py", refused)
    allowed = await registry.execute(
        use(
            "write",
            path="tests/test_handler.py",
            content="def test_handler_raises_on_failure() -> None:\n    assert True\n",
        )
    )
    show("write tests/test_handler.py", allowed)
    print("\n  The restriction is in the spec the model is shown, too:")
    spec = registry.get("write")
    assert spec is not None
    print(f"      {spec.description.splitlines()[-1]}")


async def demo_hooks(root: Path) -> None:
    rule("5. a PreToolUse hook blocks with exit 2, and its reason reaches the model")
    config = HookConfig.from_mapping(
        {
            "PreToolUse": [
                {
                    "matcher": "write|edit",
                    "hooks": [
                        {
                            "command": f"if {BLOCKING_HOOK}",
                            "name": "no-migrations",
                            "timeout": 5,
                        }
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "write",
                    "hooks": [
                        {"command": "echo 'would run prettier here' ; exit 1", "name": "formatter"}
                    ],
                }
            ],
        }
    )
    runner = HookRunner(config=config, cwd=str(root), env={"PATH": "/usr/bin:/bin"})
    print("  hook: reads the event JSON on stdin, greps it, exits 2 to refuse")
    print(
        f"  matcher 'write|edit' → applies to write: "
        f"{config.for_event(HookEvent.PRE_TOOL_USE, 'write') != ()}"
    )
    print(
        f"                       → applies to read : "
        f"{config.for_event(HookEvent.PRE_TOOL_USE, 'read') != ()}\n"
    )

    ok = await runner.fire(
        HookEvent.PRE_TOOL_USE, tool_name="write", data={"arguments": {"path": "src/app.py"}}
    )
    print(f"  write src/app.py            → blocked={ok.blocked}")

    denied = await runner.fire(
        HookEvent.PRE_TOOL_USE,
        tool_name="write",
        data={"arguments": {"path": "migrations/003_add_column.sql"}},
    )
    print(
        f"  write migrations/003…sql    → blocked={denied.blocked}, "
        f"exit={denied.runs[0].completion.exit_code}"
    )
    result = denied.as_tool_result()
    assert result is not None
    show("what the model gets back", result)

    warned = await runner.fire(HookEvent.POST_TOOL_USE, tool_name="write")
    print(
        f"\n  PostToolUse hook exits 1    → blocked={warned.blocked} "
        f"(non-2 is a warning, never a block)"
    )
    for warning in warned.warnings():
        print(f"      ⚠ {warning}")


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ronin-agents-demo-") as tmp:
        root = Path(tmp).resolve()
        seed(root)
        ctx = ToolContext(root=root, env={"PATH": "/usr/bin:/bin", "HOME": str(root)})
        base = build_registry(ctx, subagent_runner=scripted_runner)

        print(f"ronin.agents demo — temp tree at {root}")
        await demo_plan_registry(base)
        demo_plan_pinning()
        demo_definitions(root)
        await demo_path_restriction(base, root)
        await demo_hooks(root)

        rule("what this proved")
        for line in (
            "plan mode is a registry without write, and the guarantee is asserted",
            "an approved plan is a frozen value rendered into a standing objective",
            "a .md file on disk becomes a SubagentType the existing TaskTool accepts",
            "test-writer's lane is enforced by a wrapper that refuses, with a reason",
            "exit 2 blocks and its stderr becomes the model's error; exit 1 warns",
        ):
            print(f"  · {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
