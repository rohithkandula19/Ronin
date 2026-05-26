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

from .code_tools import SENSITIVE_TOOLS, build_code_tools, undo_last, unified_diff
from .config import CSKConfig
from .media import build_image_tool
from .project_memory import load_project_memory, memory_system_block, write_memory_template
from .runner import build_provider
from .streaming import LiveRenderer
from .todo import TodoStore, build_todo_tool


CODE_SYSTEM = """You are ronin in code mode — an autonomous coding agent working in a project directory.

You are given a TASK. Pursue it by reading files, searching, editing, and running
commands — one step at a time — until the task is done and verified.

Workflow:
- For any task with 3+ steps, FIRST call update_todos to lay out a short plan,
  then keep it current: exactly one item 'in_progress' at a time, flip items to
  'completed' as you finish them. Skip the todo list for trivial one-step tasks.
- Explore first: list_files / read_file / search_files to understand the code.
- Make focused edits with write_file. Preserve existing style.
- Verify your work with run_command (run the tests / the script / a lint).
- Need a logo, diagram, illustration, or placeholder art? Call generate_image
  to create it and save it into the project.
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
    streamed: bool = False  # True if the answer already streamed to the console


import hashlib as _hashlib
import json as _json
import re as _re

_MENTION_RE = _re.compile(r"(?:^|\s)@([\w./\-]+)")

# read-only tool subset (for plan mode — explore but don't mutate)
_READONLY_CODE_TOOLS = {"read_file", "list_files", "search_files", "glob"}


def _session_path(root: Path | str) -> _Path:
    """Per-repo session file under .csk/sessions/."""
    key = _hashlib.sha1(str(_Path(root).resolve()).encode()).hexdigest()[:12]
    return _Path(".csk") / "sessions" / f"code-{key}.json"


def save_session(root: Path | str, transcript: list[str]) -> None:
    p = _session_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(_json.dumps({"root": str(_Path(root).resolve()), "transcript": transcript[-40:]}),
                     encoding="utf-8")
    except OSError:
        pass


def load_session(root: Path | str) -> list[str]:
    p = _session_path(root)
    if not p.is_file():
        return []
    try:
        return list(_json.loads(p.read_text(encoding="utf-8")).get("transcript", []))
    except (OSError, ValueError):
        return []


def expand_file_mentions(task: str, root: Path | str) -> str:
    """Expand ``@path`` mentions in a request by inlining those files' contents
    (Claude Code's @-mention). Unknown paths are left as-is."""
    root_path = _Path(root).resolve()
    seen: list[str] = []
    blocks: list[str] = []
    for rel in _MENTION_RE.findall(task):
        if rel in seen:
            continue
        target = (root_path / rel).resolve()
        # stay inside the project root; only inline real files
        if (root_path == target or root_path in target.parents) and target.is_file():
            seen.append(rel)
            try:
                body = target.read_text(encoding="utf-8", errors="ignore")[:4000]
            except OSError:
                continue
            blocks.append(f"--- {rel} ---\n{body}")
    if not blocks:
        return task
    return "Referenced files:\n\n" + "\n\n".join(blocks) + "\n\n---\n\n" + task


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
    # generate_image writes a file into the project → gate it like other writes.
    gated = SENSITIVE_TOOLS | {"generate_image"}

    def gate(name: str, args: dict) -> bool:
        if name not in gated:
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
    undo_stack: list | None = None,
    history_prefix: str = "",
    read_only: bool = False,
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

    tools = build_code_tools(root, undo_stack=undo_stack)
    if read_only:
        # plan mode: explore but never mutate
        tools = [t for t in tools if t.name in _READONLY_CODE_TOOLS]
    # The live plan tracker: the agent maintains a checklist via update_todos.
    todo_store = TodoStore()
    tools = tools + [build_todo_tool(todo_store)]
    # Media: the agent can generate images into the project (free backend).
    if not read_only:
        tools = tools + [build_image_tool(root)]

    # Project memory: fold RONIN.md / CLAUDE.md / AGENTS.md into the system
    # prompt so the agent follows the repo's conventions. Announce it once (on
    # the first turn of a session / a one-shot run), not on every turn.
    system = CODE_SYSTEM
    mem = memory_system_block(root)
    if mem is not None:
        block, mem_name = mem
        system += block
        if console is not None and not history_prefix:
            console.print(f"[dim]📄 loaded project memory from [bold]{mem_name}[/bold][/dim]")

    agent = ReActAgent(
        system=system,
        tools=tools,
        provider=build_provider(config),
        max_iterations=max_iterations,
        # Long coding sessions read many files; compact old file/command output
        # so the context window survives a 25-step task.
        compact_after_tokens=120_000,
        compact_keep_recent=6,
    )

    before_tool = _selective_gate(console, yolo, _Path(root).resolve())

    # Stream the model's reasoning + summary live (the Claude-Code feel) when we
    # have a console; fall back to the step-narrator for non-interactive runs.
    renderer = LiveRenderer(console) if console is not None else None
    on_step = renderer.on_step if renderer is not None else None
    on_text = renderer.on_text if renderer is not None else None

    task = expand_file_mentions(task, root)  # inline any @path references
    prompt = f"{history_prefix}\n\nCurrent request: {task}" if history_prefix else task
    result = agent.run(prompt, on_step=on_step, before_tool=before_tool, on_text=on_text)
    if renderer is not None:
        renderer.finish()
    return CodeRunResult(
        success=result.success,
        output=result.output,
        iterations=result.iterations,
        steps=result.trace,
        usage=result.usage,
        error=result.error,
        streamed=bool(renderer and renderer.streamed_text),
    )


# In-session slash commands (the Claude-Code control surface). Both ``/cmd``
# and ``:cmd`` are accepted.
SLASH_COMMANDS: dict[str, str] = {
    "help": "show this help",
    "clear": "forget the conversation so far",
    "undo": "revert the most recent file change",
    "diff": "show the working-tree git diff",
    "model": "show the active provider + model",
    "memory": "show loaded project memory (RONIN.md / CLAUDE.md / AGENTS.md)",
    "init": "scaffold a RONIN.md project-memory file",
    "tools": "list the tools the agent can use",
    "quit": "exit the session",
}


def _show_git_diff(console: Console, root: Path | str) -> None:
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", str(_Path(root).resolve()), "diff", "--no-color"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        console.print(f"[red]git diff failed:[/red] {e}")
        return
    diff = out.stdout.strip()
    if not diff:
        console.print("[dim]working tree clean (no unstaged changes)[/dim]")
        return
    _render_diff(console, diff[:8000])
    if len(diff) > 8000:
        console.print("[dim]…(diff truncated)[/dim]")


def handle_slash_command(
    user: str,
    *,
    console: Console,
    root: Path | str,
    config: CSKConfig,
    undo_stack: list,
    transcript: list[str],
) -> str:
    """Dispatch an in-session command. Returns:
    ``"passthrough"`` (not a command → send to the agent),
    ``"handled"`` (command ran → loop again), or ``"exit"`` (quit the session).
    """
    from rich.panel import Panel

    if not (user.startswith("/") or user.startswith(":")):
        return "passthrough"
    parts = user[1:].strip().split()
    if not parts:
        return "handled"
    cmd = parts[0].lower()

    if cmd in ("q", "quit", "exit"):
        console.print("[dim]bye[/dim]")
        return "exit"
    if cmd in ("help", "h", "?"):
        console.print("[bold]commands[/bold]")
        for name, desc in SLASH_COMMANDS.items():
            console.print(f"  [cyan]/{name}[/cyan]  [dim]{desc}[/dim]")
        return "handled"
    if cmd == "clear":
        transcript.clear()
        console.print("[dim]✓ conversation cleared[/dim]")
        return "handled"
    if cmd == "undo":
        console.print(f"[yellow]↩[/yellow] {undo_last(undo_stack)}")
        return "handled"
    if cmd == "diff":
        _show_git_diff(console, root)
        return "handled"
    if cmd == "model":
        console.print(
            f"[dim]provider[/dim] [bold]{config.provider}[/bold]  ·  "
            f"[dim]model[/dim] [bold]{config.model or '(provider default)'}[/bold]"
        )
        return "handled"
    if cmd == "memory":
        found = load_project_memory(root)
        if found is None:
            console.print("[dim]no project memory file found (RONIN.md / CLAUDE.md / AGENTS.md)[/dim]")
        else:
            name, text = found
            console.print(Panel(text, title=name, border_style="cyan"))
        return "handled"
    if cmd == "init":
        path = write_memory_template(root)
        console.print(f"[green]✓[/green] project memory at [cyan]{path}[/cyan]")
        return "handled"
    if cmd == "tools":
        names = [t.name for t in build_code_tools(root)] + ["update_todos"]
        console.print("[dim]" + ", ".join(names) + "[/dim]")
        return "handled"

    console.print(f"[yellow]unknown command[/yellow] /{cmd} — try [cyan]/help[/cyan]")
    return "handled"


def run_code_session(
    config: CSKConfig,
    *,
    root: Path | str = ".",
    console: Console,
    yolo: bool = False,
    max_iterations: int = 25,
    continue_session: bool = False,
) -> None:
    """Interactive coding session (the Claude Code experience).

    A REPL: you type a request, the agent works (edits + commands gated with
    diffs), then you go again — steering across turns. Conversation context
    carries forward. Type [bold]/help[/bold] for in-session commands.
    ``continue_session`` resumes the last session's history for this repo.
    """
    from rich.panel import Panel

    undo_stack: list = []
    transcript: list[str] = load_session(root) if continue_session else []

    resumed = " · [green]resumed[/green]" if (continue_session and transcript) else ""
    console.print(Panel.fit(
        f"[bold cyan]ronin code[/bold cyan] — interactive session{resumed}\n"
        f"[dim]root: {_Path(root).resolve()} · "
        f"{'YOLO (auto-approve)' if yolo else 'writes + commands need approval'}\n"
        "type your request · [bold]@path[/bold] to reference files · "
        "[bold]/help[/bold] for commands · [bold]/quit[/bold] to exit[/dim]",
        border_style="cyan",
    ))

    while True:
        try:
            user = console.input("[bold cyan]code ›[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            return
        if not user:
            continue
        action = handle_slash_command(
            user, console=console, root=root, config=config,
            undo_stack=undo_stack, transcript=transcript,
        )
        if action == "exit":
            return
        if action == "handled":
            continue

        history_prefix = ""
        if transcript:
            history_prefix = "Conversation so far:\n" + "\n".join(transcript[-6:])

        result = run_code_agent(
            config, user, root=root, console=console, yolo=yolo,
            max_iterations=max_iterations, undo_stack=undo_stack,
            history_prefix=history_prefix,
        )
        transcript.append(f"USER: {user}")
        transcript.append(f"ASSISTANT: {result.output}")
        save_session(root, transcript)  # persist so `ronin code --continue` can resume
        # The summary already streamed inline; just show a subtle completion mark
        # instead of re-printing the whole thing.
        if result.streamed:
            console.print("\n[bold green]✅[/bold green] [dim]done[/dim]\n")
        else:
            console.print(f"\n[bold green]✅[/bold green] {result.output}\n")
