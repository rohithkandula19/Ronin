"""Shared pieces for the agents tests: a real registry in a temp tree, no shell.

Everything here is offline. The only subprocess in this suite is the ``sh -c``
one-liner in ``test_hooks.py``, which is deliberate and local.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from ronin.agents.hooks import HookCompletion, HookInvocation
from ronin.core.types import DangerLevel, ToolResult, ToolUse
from ronin.tools.base import Example, Tool, ToolContext
from ronin.tools.registry import ToolRegistry, build_registry
from ronin.tools.task import SubagentType


def context(root: Path) -> ToolContext:
    """A context rooted in the temp tree, with a clean environment."""
    return ToolContext(
        root=root,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(root), "LANG": "C.UTF-8"},
    )


async def scripted_runner(prompt: str, subagent: SubagentType) -> str:
    return f"[{subagent.name}] {prompt}"


def registry(root: Path, *, with_task: bool = True) -> ToolRegistry:
    """The real tool registry: files, search, todo_write and (optionally) task."""
    return build_registry(
        context(root),
        subagent_runner=scripted_runner if with_task else None,
    )


def use(name: str, **args: Any) -> ToolUse:
    return ToolUse(id=f"call_{name}", name=name, arguments=args)


def seed(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.py").write_text("def handler():\n    return None\n")


class MislabelledWrite(Tool):
    """A mutating tool that claims to be read-only.

    Exists to prove the plan-mode check does not rely on ``danger_level`` alone:
    a tool can lie, and the name check is what catches it.
    """

    name = "write"
    summary = "Pretends to be harmless."
    when_to_use = "Never; this is a test fixture."
    danger_level = DangerLevel.READ_ONLY
    schema: ClassVar[Mapping[str, Any]] = {"type": "object", "properties": {}}
    examples: ClassVar[tuple[Example, ...]] = (Example(call="write()", result="ok"),)

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, content="pretended to write")


class FakeProcess:
    """An injected hook runner: scripted completions, and a record of the calls.

    Keyed by command so a config with several hooks can script each one; a command
    with no entry gets exit 0, because "the hook passed" is the boring default.
    """

    def __init__(self, replies: Mapping[str, HookCompletion] | None = None) -> None:
        self.replies = dict(replies or {})
        self.calls: list[HookInvocation] = []

    async def __call__(self, invocation: HookInvocation) -> HookCompletion:
        self.calls.append(invocation)
        return self.replies.get(invocation.command, HookCompletion(exit_code=0))

    @property
    def commands(self) -> Sequence[str]:
        return [call.command for call in self.calls]
