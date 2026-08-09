"""See the tool layer work: ``uv run python -m ronin.tools.demo``.

Runs in a throwaway temp directory against real files and a real shell, so what you
see is what a model would get. The three rules the layer is built on each get a
demonstration, including the refusals — a tool layer's refusals are half its behaviour
and they are the half you cannot see from a passing test suite.
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..core.types import ToolResult, ToolUse
from .base import ToolContext
from .registry import ToolRegistry, build_registry
from .shell import PersistentShell, ShellSession
from .task import SubagentType


def rule(title: str) -> None:
    print(f"\n{'─' * 78}\n{title}\n{'─' * 78}")


def show(label: str, result: ToolResult) -> None:
    mark = "✔" if result.ok else "✗"
    body = result.content if result.ok else result.error
    lines = body.split("\n")
    head = lines[:6]
    print(f"  {mark} {label}")
    for line in head:
        print(f"      {line}")
    if len(lines) > len(head):
        print(f"      … ({len(lines) - len(head)} more lines)")


async def scripted_runner(prompt: str, subagent: SubagentType) -> str:
    """Stands in for the wiring, which lives in ``ronin.session``.

    This demo is about the *tools*; ``uv run python -m ronin.session_demo`` shows the
    real nested turn on the fast model.
    """
    return (
        f"[{subagent.name} subagent, tools={', '.join(subagent.tools)}]\n"
        f"Asked: {prompt}\n"
        "Answer: src/app.py:2 and src/util.py:2 both return None."
    )


async def fake_fetch(url: str) -> tuple[str, str]:
    return "text/html", "<h1>Changelog</h1><p>3.0 removed the sync client.</p>"


async def fake_extract(prompt: str, text: str) -> str:
    return "3.0 removed the sync client."


async def fake_search(query: str) -> Sequence[Mapping[str, str]]:
    return [{"title": "ripgrep guide", "url": "https://x.test/rg", "snippet": "--multiline"}]


def seed(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src/app.py").write_text("def handler():\n    return None\n")
    (root / "src/util.py").write_text("def helper():\n    return None\n")
    (root / ".gitignore").write_text("build/\n")
    (root / "build").mkdir(exist_ok=True)
    (root / "build/generated.py").write_text("# generated\n")


async def demo_write_guard(registry: ToolRegistry, ctx: ToolContext) -> None:
    rule("1. write refuses a file it has not read — the rule that saves work")
    print("  A model that has read the file knows what it is replacing. One that has")
    print("  not is guessing, and a full overwrite of a guess destroys work.\n")

    blocked = await registry.execute(_use("write", path="src/app.py", content="# oops\n"))
    show("write src/app.py (never read)", blocked)

    read = await registry.execute(_use("read", path="src/app.py"))
    show("read src/app.py", read)

    allowed = await registry.execute(
        _use("write", path="src/app.py", content="def handler():\n    return []\n")
    )
    show("write src/app.py (after reading)", allowed)


async def demo_edit(registry: ToolRegistry) -> None:
    rule("2. edit matches exactly, and ambiguity is an error")
    print("  `return None` appears in two files and twice in this one. A tool that")
    print("  picked one would corrupt a file and report success.\n")

    await registry.execute(_use("read", path="src/util.py"))
    await registry.execute(
        _use(
            "write",
            path="src/util.py",
            content="def helper():\n    return None\n\ndef other():\n    return None\n",
        )
    )

    ambiguous = await registry.execute(
        _use("edit", path="src/util.py", old_string="return None", new_string="return []")
    )
    show("edit with an ambiguous old_string", ambiguous)

    scoped = await registry.execute(
        _use(
            "edit",
            path="src/util.py",
            old_string="def other():\n    return None",
            new_string="def other():\n    return []",
        )
    )
    show("edit with surrounding context (now unique)", scoped)

    near_miss = await registry.execute(
        _use("edit", path="src/util.py", old_string="def  helper():", new_string="def helper2():")
    )
    show("edit with a near-miss (double space) — never fuzzy matched", near_miss)


async def demo_shell(registry: ToolRegistry) -> None:
    rule("3. bash is one persistent shell")
    print("  cd, export and an activated venv survive across calls, because it is the")
    print("  same process throughout. With a subprocess per call, pwd would print the")
    print("  original directory and the model would have to re-derive everything.\n")

    show("cd src", await registry.execute(_use("bash", command="cd src", description="Move")))
    show("pwd", await registry.execute(_use("bash", command="pwd", description="Where am I")))
    show(
        "export then read back",
        await registry.execute(
            _use("bash", command="export TOK=kept; echo $TOK", description="Set and read")
        ),
    )

    print()
    for command in ("grep -r TODO .", "find . -name '*.py'", "cat src/app.py", "vim x.py"):
        result = await registry.execute(_use("bash", command=command, description="x"))
        show(f"bash {command!r}", result)


async def demo_search(registry: ToolRegistry) -> None:
    rule("4. search wraps ripgrep — gitignore for free, output capped")
    show(
        "glob **/*.py",
        await registry.execute(_use("glob", pattern="**/*.py")),
    )
    print("      ↑ build/generated.py is absent: .gitignore honoured without us knowing")
    show(
        "grep 'return' mode=files",
        await registry.execute(_use("grep", pattern="return", mode="files")),
    )


async def demo_plan_and_subagent(registry: ToolRegistry) -> None:
    rule("5. the plan, and a subagent whose context you never pay for")
    show(
        "todo_write",
        await registry.execute(
            _use(
                "todo_write",
                todos=[
                    {"content": "Read both files", "status": "completed"},
                    {"content": "Replace the None returns", "status": "in_progress"},
                    {"content": "Run the tests", "status": "pending"},
                ],
            )
        ),
    )
    show(
        "todo_write with two things in progress",
        await registry.execute(
            _use(
                "todo_write",
                todos=[
                    {"content": "A", "status": "in_progress"},
                    {"content": "B", "status": "in_progress"},
                ],
            )
        ),
    )
    show(
        "task(explore)",
        await registry.execute(
            _use("task", prompt="Where do we return None?", subagent_type="explore")
        ),
    )


async def demo_net(registry: ToolRegistry) -> None:
    rule("6. the network, with the fast model in front of it")
    print("  web_fetch returns the answer, not the page: a page is tens of thousands of")
    print("  tokens of navigation to answer one question.\n")
    show(
        "web_fetch",
        await registry.execute(
            _use("web_fetch", url="https://x.test/CHANGELOG", prompt="What changed in 3.0?")
        ),
    )
    show(
        "web_fetch localhost",
        await registry.execute(
            _use("web_fetch", url="http://localhost:3000/", prompt="anything")
        ),
    )
    show("web_search", await registry.execute(_use("web_search", query="ripgrep multiline")))


def _use(name: str, **args: Any) -> ToolUse:
    return ToolUse(id=f"demo_{name}", name=name, arguments=args)


async def main() -> int:
    print("ronin.tools — offline demo in a temp directory. Real files, real shell.")
    with tempfile.TemporaryDirectory(prefix="ronin-tools-demo-") as tmp:
        root = Path(tmp)
        seed(root)
        ctx = ToolContext(root=root)
        shell = ShellSession(shell=PersistentShell(cwd=root))
        clock = time.monotonic
        registry = build_registry(
            ctx,
            shell=shell,
            subagent_runner=scripted_runner,
            fetch=fake_fetch,
            extract=fake_extract,
            search=fake_search,
            clock=clock,
        )
        try:
            names = [spec.name for spec in registry.specs()]
            print(f"  {len(names)} tools: {', '.join(names)}\n")
            await demo_write_guard(registry, ctx)
            await demo_edit(registry)
            await demo_shell(registry)
            await demo_search(registry)
            await demo_plan_and_subagent(registry)
            await demo_net(registry)
        finally:
            await shell.close()
    rule("done")
    print("Every refusal above is a message written for the model to act on, not a")
    print("status code. That is most of what a tool layer is.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
