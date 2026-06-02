"""``ronin eval`` — measure agent quality with objective, deterministic tasks.

Each task gives ronin a real job in a throwaway sandbox and checks the *outcome*
(file created? right answer? tool used?) rather than asking an LLM to judge.
That makes the score trustworthy, reproducible, and runnable on **any** provider
(no judge model / Anthropic key required) — so you can compare Claude vs Gemini
vs Cerebras on the exact same bar.

This is the "how do you know your agent works?" artifact.
"""
from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from rich.console import Console

from .code_mode import CodeRunResult, run_code_agent
from .config import RoninConfig

# A focused harness prompt: act via tools, then answer. Fairer for agentic tasks
# than the conversation-first unified prompt (which under-uses tools).
_EVAL_SYSTEM = """You are ronin, an autonomous agent in a project directory.

Do exactly what the user asks by ACTING, not just describing:
- To create or change a file, CALL write_file / edit_file (don't print the code).
- To inspect the project, CALL read_file / list_files.
- For a pure question (math, a fact, a one-word reply), just answer in plain text.

Be direct. When the task is done, give a one-line confirmation or the answer."""


@dataclass
class EvalTask:
    id: str
    prompt: str
    check: Callable[[CodeRunResult, Path], bool]
    note: str = ""
    setup: Callable[[Path], None] | None = None  # seed the sandbox before the run


@dataclass
class TaskOutcome:
    task: EvalTask
    passed: bool
    iterations: int
    elapsed: float
    tokens: int
    error: str | None = None


def _read(root: Path, name: str) -> str:
    p = root / name
    return p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else ""


def default_tasks() -> list[EvalTask]:
    """A compact, representative battery: reasoning, single + multi-file codegen,
    tool use (read/list), and instruction-following."""
    return [
        EvalTask(
            "arithmetic",
            "What is 17 * 23? Reply with just the number.",
            lambda r, root: "391" in r.output,
            "reasoning",
        ),
        EvalTask(
            "write_file",
            "Create a file named greeting.txt whose only contents are the word: ronin",
            lambda r, root: "ronin" in _read(root, "greeting.txt").lower(),
            "file write (tool use)",
        ),
        EvalTask(
            "codegen",
            "Write a Python file sum_list.py with a function total(xs) that returns the sum of a list of numbers.",
            lambda r, root: "def total" in _read(root, "sum_list.py"),
            "code generation",
        ),
        EvalTask(
            "read_grounded",
            "Read config.json in this directory and tell me the value of the 'port' field.",
            lambda r, root: "8080" in r.output,
            "read + grounded answer",
            setup=lambda root: (root / "config.json").write_text('{"port": 8080}\n'),
        ),
        EvalTask(
            "multi_file",
            "Create add.py with a function add(a, b) returning a+b, and a separate "
            "test_add.py that imports add and asserts add(2,3)==5.",
            lambda r, root: "def add" in _read(root, "add.py")
            and "add" in _read(root, "test_add.py")
            and "import" in _read(root, "test_add.py"),
            "multi-file task",
        ),
        EvalTask(
            "instruction",
            "Reply with exactly the single word: acknowledged",
            lambda r, root: r.output.strip().lower().strip(".!") == "acknowledged",
            "instruction-following",
        ),
    ]


def run_eval(
    config: RoninConfig,
    tasks: list[EvalTask] | None = None,
    *,
    model: str | None = None,
    max_iterations: int = 12,
) -> list[TaskOutcome]:
    """Run each task in its own sandbox on ``config`` (optionally overriding the
    model) and score the outcome objectively."""
    tasks = tasks or default_tasks()
    cfg = config.model_copy(update={"model": model}) if model else config
    outcomes: list[TaskOutcome] = []
    for task in tasks:
        sandbox = Path(tempfile.mkdtemp(prefix="ronin-eval-"))
        try:
            if task.setup:
                task.setup(sandbox)
            t0 = time.time()
            res = run_code_agent(
                cfg, task.prompt, root=sandbox, console=None, yolo=True,
                max_iterations=max_iterations, base_system=_EVAL_SYSTEM,
                include_image_tool=False,
            )
            elapsed = time.time() - t0
            try:
                passed = bool(task.check(res, sandbox))
            except Exception:  # noqa: BLE001
                passed = False
            tokens = (res.usage or {}).get("input_tokens", 0) + (res.usage or {}).get("output_tokens", 0)
            outcomes.append(TaskOutcome(task, passed, res.iterations, elapsed, tokens,
                                        error=res.error if not res.success else None))
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)
    return outcomes


def render_report(console: Console, config: RoninConfig, outcomes: list[TaskOutcome],
                  *, model: str | None = None) -> float:
    """Print a scored table + summary. Returns the success rate (0..1)."""
    from rich.table import Table

    from .theme import ACCENT, ERR, MUTE, OK

    shown_model = model or config.resolved_model()
    table = Table(title=None, box=None, padding=(0, 2))
    table.add_column("", width=2)
    table.add_column("task", style=ACCENT)
    table.add_column("what it tests", style=MUTE)
    table.add_column("steps", justify="right", style=MUTE)
    table.add_column("tokens", justify="right", style=MUTE)
    table.add_column("time", justify="right", style=MUTE)

    for o in outcomes:
        mark = f"[{OK}]✓[/{OK}]" if o.passed else f"[{ERR}]✗[/{ERR}]"
        table.add_row(mark, o.task.id, o.task.note, str(o.iterations),
                      f"{o.tokens:,}", f"{o.elapsed:.1f}s")

    passed = sum(1 for o in outcomes if o.passed)
    total = len(outcomes)
    rate = passed / total if total else 0.0
    tot_tokens = sum(o.tokens for o in outcomes)
    tot_time = sum(o.elapsed for o in outcomes)

    console.print()
    console.print(f"[bold]ronin eval[/bold]  [dim]·  {config.provider} · {shown_model}[/dim]")
    console.print(table)
    bar_color = OK if rate >= 0.8 else (ACCENT if rate >= 0.5 else ERR)
    console.print()
    console.print(
        f"  [bold {bar_color}]{passed}/{total} passed[/bold {bar_color}] "
        f"[dim]({rate * 100:.0f}%)  ·  {tot_tokens:,} tokens  ·  {tot_time:.1f}s total[/dim]"
    )
    console.print()
    return rate
