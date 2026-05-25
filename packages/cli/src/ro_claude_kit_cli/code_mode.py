"""``csk code`` — the coding-agent surface (Claude Code / Cline shaped).

Give it a task. It reads your files, reasons, edits code, runs commands —
narrating each step. Every *write* and *shell command* is gated behind your
approval by default (read operations run freely). ``--yolo`` auto-approves
everything for trusted, sandboxed runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from rich.console import Console

from ro_claude_kit_agent_patterns import ReActAgent, Step
from ro_claude_kit_hardening import InjectionScanner

from pathlib import Path as _Path

from .agent_mode import _narrate
from .code_tools import SENSITIVE_TOOLS, build_code_tools, unified_diff
from .config import CSKConfig
from .runner import build_provider


CODE_SYSTEM = """You are csk in code mode — an autonomous coding agent working in a project directory.

You are given a TASK. Pursue it by reading files, searching, editing, and running
commands — one step at a time — until the task is done and verified.

Workflow:
- Explore first: list_files / read_file / search_files to understand the code.
- Make focused edits with write_file. Preserve existing style.
- Verify your work with run_command (run the tests / the script / a lint).
- When done, summarize exactly what you changed and how you verified it.

Constraints:
- write_file and run_command require human approval — expect to be told 'no'
  sometimes; adapt.
- Stay inside the project root. Don't touch .git.
- If the task is ambiguous or unsafe, stop and explain rather than guessing.
"""


@dataclass
class CodeRunResult:
    success: bool
    output: str
    iterations: int
    steps: list[Step] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    blocked: bool = False


def _render_diff(console: Console, diff: str) -> None:
    """Print a unified diff with +/- line coloring (Claude-Code style preview)."""
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            console.print(f"[green]{line}[/green]")
        elif line.startswith("-") and not line.startswith("---"):
            console.print(f"[red]{line}[/red]")
        elif line.startswith("@@"):
            console.print(f"[cyan]{line}[/cyan]")
        else:
            console.print(f"[dim]{line}[/dim]")


def _selective_gate(console: Console | None, yolo: bool, root: _Path) -> Callable[[str, dict], bool]:
    """Auto-approve read tools; gate SENSITIVE_TOOLS unless yolo.

    For write_file / edit_file, render a unified diff of the proposed change
    *before* asking — so you approve a visible diff, not a blind action.
    """
    def gate(name: str, args: dict) -> bool:
        if name not in SENSITIVE_TOOLS:
            return True  # reads run freely
        if yolo:
            return True
        if console is None:
            return False  # no way to ask → deny by default

        # Show what's actually about to happen.
        if name in ("write_file", "edit_file"):
            rel = args.get("path", "?")
            target = (root / rel)
            before = target.read_text(encoding="utf-8") if target.is_file() else ""
            if name == "write_file":
                after = args.get("content", "")
            else:  # edit_file
                old, new = args.get("old_string", ""), args.get("new_string", "")
                after = before.replace(old, new, 1) if old in before else before
            console.print(f"[yellow]?[/yellow] [bold]{name}[/bold] [cyan]{rel}[/cyan]:")
            _render_diff(console, unified_diff(rel, before, after))
        elif name == "run_command":
            console.print(f"[yellow]?[/yellow] [bold]run[/bold]: [cyan]{args.get('command')}[/cyan]")
        else:
            console.print(f"[yellow]?[/yellow] {name}({args})")

        console.print("approve? [y/N] ", end="")
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("y", "yes")

    return gate


def run_code_agent(
    config: CSKConfig,
    task: str,
    *,
    root: Path | str = ".",
    console: Console | None = None,
    yolo: bool = False,
    max_iterations: int = 25,
) -> CodeRunResult:
    scan = InjectionScanner().scan(task)
    if scan.flagged:
        return CodeRunResult(
            success=False,
            output="[blocked] your task was flagged as a potential prompt-injection attempt.",
            iterations=0,
            error=f"injection-scan flagged: {[h['label'] for h in scan.hits]}",
            blocked=True,
        )

    tools = build_code_tools(root)
    agent = ReActAgent(
        system=CODE_SYSTEM,
        tools=tools,
        provider=build_provider(config),
        max_iterations=max_iterations,
    )

    on_step = _narrate(console) if console is not None else None
    before_tool = _selective_gate(console, yolo, _Path(root).resolve())

    result = agent.run(task, on_step=on_step, before_tool=before_tool)
    return CodeRunResult(
        success=result.success,
        output=result.output,
        iterations=result.iterations,
        steps=result.trace,
        usage=result.usage,
        error=result.error,
    )
