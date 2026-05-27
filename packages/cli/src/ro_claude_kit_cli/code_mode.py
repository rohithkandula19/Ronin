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
from .prompt_box import read_prompt
from .runner import build_provider
from .streaming import LiveRenderer
from .theme import ACCENT as _ACCENT
from .todo import TodoStore, build_todo_tool


def _welcome(console: "Console", config: CSKConfig, root, yolo: bool, *, title: str, hint: str) -> None:
    """A soft, premium welcome header with the gradient ronin wordmark."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.text import Text

    from .theme import ACCENT, MUTE, SOFT, gradient_text

    head = Text()
    head.append_text(gradient_text("✦ ronin"))
    sub = title
    for p in ("ronin — ", "ronin "):
        if sub.startswith(p):
            sub = sub[len(p):]
            break
    if sub:
        head.append("  ")
        head.append(sub, style=SOFT)

    def row(label: str, value: str) -> Text:
        t = Text("  ")
        t.append(f"{label:<6}", style=MUTE)
        t.append(value, style=SOFT)
        return t

    mode = "auto-approve (YOLO)" if yolo else "edits + commands need approval"
    body = Group(
        head,
        Text(""),
        row("cwd", str(_Path(root).resolve())),
        row("model", f"{config.provider} · {config.resolved_model()}"),
        row("mode", mode),
        Text(""),
        Text("  " + hint, style=MUTE),
    )
    console.print(Panel.fit(body, border_style=ACCENT, padding=(1, 2)))
    console.print()


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
    # Only real mutations (file edits + shell) are gated. Image generation is a
    # free, low-risk creative action — it runs without an approval prompt.
    def gate(name: str, args: dict) -> bool:
        if name not in SENSITIVE_TOOLS:
            return True  # reads + media generation run freely
        if yolo:
            return True
        if console is None:
            return False  # no way to ask → deny by default

        # Show what's actually about to happen, then ask.
        if name in ("write_file", "edit_file"):
            rel = args.get("path", "?")
            target = (root / rel)
            before = target.read_text(encoding="utf-8") if target.is_file() else ""
            if name == "write_file":
                after = args.get("content", "")
            else:  # edit_file
                old, new = args.get("old_string", ""), args.get("new_string", "")
                after = before.replace(old, new, 1) if old in before else before
            verb = "Write" if name == "write_file" else "Edit"
            console.print(f"  [yellow]›[/yellow] [bold]{verb}[/bold] [cyan]{rel}[/cyan]")
            _render_diff(console, unified_diff(rel, before, after))
        elif name == "run_command":
            console.print(f"  [yellow]›[/yellow] [bold]Run[/bold] [cyan]{args.get('command')}[/cyan]")
        elif name == "multi_edit":
            console.print(f"  [yellow]›[/yellow] [bold]Edit[/bold] [cyan]{args.get('path', '?')}[/cyan] "
                          f"[grey50]({len(args.get('edits', []))} change(s))[/grey50]")
        else:
            console.print(f"  [yellow]›[/yellow] [bold]{name}[/bold] [grey50]{args}[/grey50]")

        console.print("    [yellow]approve?[/yellow] [grey50]y / N[/grey50] ", end="")
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
    extra_tools: list | None = None,
    extra_system: str = "",
    include_image_tool: bool = True,
    base_system: str | None = None,
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
    if not read_only and include_image_tool:
        tools = tools + [build_image_tool(root)]
    # Extra tools (e.g. the unified agent's media + data tools).
    if extra_tools and not read_only:
        tools = tools + list(extra_tools)

    # Project memory: fold RONIN.md / CLAUDE.md / AGENTS.md into the system
    # prompt so the agent follows the repo's conventions. Announce it once (on
    # the first turn of a session / a one-shot run), not on every turn.
    system = base_system or CODE_SYSTEM
    if extra_system:
        system += "\n\n" + extra_system
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
    if renderer is not None:
        renderer.start()  # soft "thinking…" spinner until the first token
    try:
        result = agent.run(prompt, on_step=on_step, before_tool=before_tool, on_text=on_text)
    except Exception as e:  # noqa: BLE001 — never crash the session on a provider/network error
        if renderer is not None:
            renderer.finish()
        from .runner import _friendly_provider_error
        return CodeRunResult(
            success=False,
            output=_friendly_provider_error(e, config),
            iterations=0,
            error=f"{e.__class__.__name__}: {e}",
        )
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

    stripped = user.strip()
    if not (stripped.startswith("/") or stripped.startswith(":")):
        return "passthrough"
    first = stripped.split()[0]
    # A real command is "/cmd" or ":cmd" (letters/digits/hyphen) — NOT a leading
    # filesystem path like "/Users/me/proj" or "/home/x". Those go to the agent.
    if "/" in first[1:] or "\\" in first:
        return "passthrough"
    parts = stripped[1:].split()
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
        # persistent cross-session memory about the user
        from .memory_store import load_memories
        mems = load_memories()
        if mems:
            body = "\n".join(f"[#c678dd]•[/#c678dd] {m['text']}" for m in mems[-40:])
            console.print(Panel(body, title=f"🧠 remembers about you ({len(mems)})", border_style="#c678dd"))
        else:
            console.print("[dim]no long-term memories yet — I'll save durable facts as we talk.[/dim]")
        # project memory file
        found = load_project_memory(root)
        if found is not None:
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
    undo_stack: list = []
    transcript: list[str] = load_session(root) if continue_session else []

    resumed = " · resumed" if (continue_session and transcript) else ""
    _welcome(console, config, root, yolo,
             title=f"ronin code{resumed}",
             hint="your request · @path to reference files · /help · /undo · /quit")

    while True:
        try:
            user = read_prompt(console, hint="/help · @path to add files · ⌃c to quit").strip()
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
        if not result.success:
            console.print(f"\n{result.output}\n")   # clean error (e.g. rate-limit), session continues
        elif result.streamed:
            console.print("\n[bold green]✅[/bold green] [dim]done[/dim]\n")
        else:
            console.print(f"\n[bold green]✅[/bold green] {result.output}\n")


UNIFIED_SYSTEM = """You are ronin — one helpful assistant living in the user's terminal.

FIRST AND ALWAYS: reply to the user. Be conversational and direct. NEVER return an
empty response. If it's a question or chit-chat ("hey", "what is groq", "how are you"),
just ANSWER it in plain text — do not call any tool and do not go silent.

You ALSO have tools. Use them ONLY when the request clearly needs them:
- Coding — read_file / write_file / edit_file / multi_edit / glob / search_files / run_command:
  for reading, editing, or running code. Explore first, make focused edits, then verify by
  running tests/commands. Edits and shell commands are shown to the user for approval.
- Media — generate_image / generate_video / speak: when asked to make a picture, video, or speech.
- Data — the configured service tools (Stripe / Linear / …): for questions about their business data.
- remember — save a durable fact about the user for future sessions.

Pick the right capability and don't confuse them (e.g. "write code to make an image" means
WRITE CODE, not generate_image). For multi-step coding tasks, use update_todos to plan.
Keep replies tight. Media you generate is shown to the user automatically."""


def run_unified_session(
    config: CSKConfig,
    *,
    root: Path | str = ".",
    console: Console,
    yolo: bool = False,
    max_iterations: int = 25,
    continue_session: bool = False,
) -> None:
    """The single front door: one conversation that talks, generates media, AND
    writes/runs code (edits + commands gated). Bare ``ronin`` opens this."""
    from rich.panel import Panel

    from .media import build_media_tools, show_artifacts
    from .memory_store import build_remember_tool, load_memories, memory_prompt_block
    from .tools import build_tools

    undo_stack: list = []
    transcript: list[str] = load_session(root) if continue_session else []
    artifacts: list = []
    # media (image/video/speech) + data (stripe/linear/…) + persistent memory,
    # layered on the coding agent's machinery (streaming, diffs, gate, todos).
    media_tools = build_media_tools(artifacts, root=root)
    data_tools = build_tools(config)
    extra = media_tools + data_tools + [build_remember_tool()]
    # cross-session memory: what ronin remembers about the user
    mem_block = memory_prompt_block()
    n_mem = len(load_memories())

    resumed = " · resumed" if (continue_session and transcript) else ""
    _welcome(console, config, root, yolo,
             title=f"ronin — one assistant for everything{resumed}",
             hint="talk · code · make images/video/voice · query data · @path · /help · /quit")
    if n_mem:
        console.print(f"  [#6b7089]🧠 {n_mem} thing(s) remembered about you · [bold]/memory[/bold] to view[/#6b7089]\n")

    while True:
        try:
            user = read_prompt(console, hint="/ for commands · @ for files · ⌃c to quit").strip()
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
            history_prefix=history_prefix, extra_tools=extra,
            base_system=UNIFIED_SYSTEM, extra_system=mem_block, include_image_tool=False,
        )
        transcript.append(f"USER: {user}")
        transcript.append(f"ASSISTANT: {result.output}")
        save_session(root, transcript)
        # auto-remember durable facts — only on substantive turns (saves rate limit
        # on trivial ones like "hey", which never yield facts anyway)
        if result.success and len(user) > 20:
            from .memory_store import auto_extract_background
            auto_extract_background(config, f"USER: {user}\nASSISTANT: {result.output}")
        if not result.success:
            console.print(f"\n{result.output}\n")   # clean error (e.g. rate-limit), session continues
        elif result.streamed:
            console.print("\n[bold green]✅[/bold green] [dim]done[/dim]\n")
        else:
            console.print(f"\n[bold green]✅[/bold green] {result.output}\n")
        show_artifacts(artifacts)  # display any image/video produced
