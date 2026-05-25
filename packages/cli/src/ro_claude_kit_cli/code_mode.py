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

from .agent_mode import _narrate
from .code_tools import SENSITIVE_TOOLS, build_code_tools
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


def _selective_gate(console: Console | None, yolo: bool) -> Callable[[str, dict], bool]:
    """Auto-approve read tools; gate SENSITIVE_TOOLS unless yolo."""
    def gate(name: str, args: dict) -> bool:
        if name not in SENSITIVE_TOOLS:
            return True  # reads run freely
        if yolo:
            return True
        if console is None:
            return False  # no way to ask → deny by default
        # Show the diff-relevant payload so the user knows what they're approving.
        if name == "write_file":
            preview = f"write {args.get('path')} ({len(args.get('content', ''))} chars)"
        elif name == "run_command":
            preview = f"run: {args.get('command')}"
        else:
            preview = f"{name}({args})"
        console.print(f"[yellow]?[/yellow] approve — [bold]{preview}[/bold]? [y/N] ", end="")
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
    before_tool = _selective_gate(console, yolo)

    result = agent.run(task, on_step=on_step, before_tool=before_tool)
    return CodeRunResult(
        success=result.success,
        output=result.output,
        iterations=result.iterations,
        steps=result.trace,
        usage=result.usage,
        error=result.error,
    )
