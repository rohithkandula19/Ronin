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

from ronin_agent_patterns import ReActAgent, Step
from ronin_hardening import InjectionScanner

from pathlib import Path as _Path

from .code_tools import SENSITIVE_TOOLS, build_code_tools, undo_last, unified_diff
from .config import RoninConfig
from .media import build_image_tool
from .project_memory import load_project_memory, memory_system_block, write_memory_template
from .self_command import detect_self_command
from .prompt_box import read_prompt
from .runner import build_provider
from .streaming import LiveRenderer
from .theme import ACCENT as _ACCENT
from .theme import ERR as _ERR
from .theme import MUTE as _MUTE
from .theme import OK as _OK
from .todo import TodoStore, build_todo_tool


def _welcome(console: "Console", config: RoninConfig, root, yolo: bool, *, title: str, hint: str) -> None:
    """Launch welcome: the **dancing panda** animates inline beside the
    version/model/cwd block (ronin's signature mascot), then a one-line tips
    row — Claude-Code-minimal chrome, but the panda still dances on every
    launch. On a non-TTY (tests/pipes) the panda renders as a single static
    frame, so output stays deterministic. (``title``/``hint`` are accepted for
    call-site compatibility; the per-prompt hint line carries shortcuts now.)"""
    from rich.text import Text

    from . import __version__
    from .panda_art import animate_inline
    from .theme import ACCENT, MUTE, SOFT

    info = Text()
    info.append("ronin ", style=f"bold {ACCENT}")
    info.append(f"v{__version__}\n", style=MUTE)
    info.append(f"{config.provider} · {config.resolved_model()}\n", style=SOFT)
    info.append(str(_Path(root).resolve()), style=MUTE)
    if yolo:
        info.append("  · auto-approve (YOLO)", style="yellow")

    console.print()
    animate_inline(console, info, activity="dancing", loops=3)
    console.print()
    console.print(Text("  /help for commands · @ to add files · shift+tab to cycle mode",
                       style=MUTE))
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
- If the task is ambiguous or unsafe and you can't resolve it by reading the
  code, call ask_user with ONE sharp question (offer options when you can)
  before doing significant work — guessing wrong wastes more time than asking.
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
import os as _os
import re as _re

_MENTION_RE = _re.compile(r"(?:^|\s)@([\w./\-]+)")
# @-URL mentions: @https://… pulls the page's readable text into context, the
# web counterpart of @file. Matched before file mentions (the file regex stops
# at the ':' so it never swallows a URL).
_URL_MENTION_RE = _re.compile(r"(?:^|\s)@(https?://[^\s]+)")

# read-only tool subset (for plan mode — explore but don't mutate)
_READONLY_CODE_TOOLS = {"read_file", "list_files", "search_files", "glob"}


def _session_path(root: Path | str) -> _Path:
    """Per-repo session file under .ronin/sessions/."""
    key = _hashlib.sha1(str(_Path(root).resolve()).encode()).hexdigest()[:12]
    return _Path(".ronin") / "sessions" / f"code-{key}.json"


def save_session(root: Path | str, transcript: list[str]) -> None:
    """Archive the FULL current-session transcript under .ronin/sessions/ — every
    session is kept as its own file (resumable via /resume), never overwritten."""
    from .sessions import save_session as _archive
    _archive(root, transcript)


def load_session(root: Path | str) -> list[str]:
    """Reload the MOST RECENT session for this project and continue it (used by
    --continue); pick an older one with /resume. Returns its transcript."""
    from .sessions import latest_session, load_session as _load, set_current_session
    sid = latest_session(root)
    if not sid:
        return []
    set_current_session(sid)   # continue that session instead of forking a new file
    return _load(sid)


def expand_file_mentions(task: str, root: Path | str, *, offline: bool = False) -> str:
    """Expand ``@path`` and ``@https://…`` mentions in a request by inlining the
    referenced files' contents and web pages' readable text (Claude Code's
    @-mention, extended to URLs). Unknown paths and unreachable URLs are left
    as-is. ``offline`` skips all URL fetches (no network egress)."""
    root_path = _Path(root).resolve()

    # --- @-URL mentions: fetch each page's readable text (skipped when offline)
    url_blocks: list[str] = []
    if not offline:
        seen_urls: list[str] = []
        urls = _URL_MENTION_RE.findall(task)
        if urls:
            from .web_tools import fetch_url
            for url in urls:
                url = url.rstrip(".,);]")  # trim trailing punctuation from prose
                if url in seen_urls:
                    continue
                seen_urls.append(url)
                text = fetch_url(url, max_chars=4000)
                if text.startswith("ERROR:"):
                    continue
                url_blocks.append(f"--- {url} ---\n{text}")

    # --- @-file mentions: inline real files inside the project root
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

    if not blocks and not url_blocks:
        return task
    parts = []
    if url_blocks:
        parts.append("Referenced web pages:\n\n" + "\n\n".join(url_blocks))
    if blocks:
        parts.append("Referenced files:\n\n" + "\n\n".join(blocks))
    return "\n\n".join(parts) + "\n\n---\n\n" + task


def expand_custom_command(user: str, root: Path | str) -> str | None:
    """Custom slash commands: a file ``.ronin/commands/NAME.md`` makes ``/NAME``
    available. Its contents are a prompt template sent to the agent, with
    ``$ARGUMENTS`` replaced by whatever follows ``/NAME`` (or appended if absent).

    Returns the expanded prompt, or ``None`` when this isn't a custom command
    (builtins and plain text fall through untouched)."""
    if not user.startswith("/"):
        return None
    parts = user[1:].split(maxsplit=1)
    if not parts:
        return None
    name, rest = parts[0], (parts[1] if len(parts) > 1 else "")
    if "/" in name or "\\" in name or name.lower() in SLASH_COMMANDS:
        return None  # a path, or a builtin command — not a custom one
    cmd_file = _Path(root) / ".ronin" / "commands" / f"{name}.md"
    if not cmd_file.is_file():
        return None
    try:
        template = cmd_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if "$ARGUMENTS" in template:
        return template.replace("$ARGUMENTS", rest)
    return f"{template}\n\n{rest}".strip() if rest else template


def _list_provider_models(config: RoninConfig) -> list[str]:
    """Best-effort list of model ids for the active provider.

    OpenAI-compatible providers expose ``GET {base_url}/models``; Anthropic has
    no list endpoint so we return a short curated set. Network/parse failures
    return an empty list (the caller shows a friendly hint)."""
    if config.provider == "anthropic":
        return ["claude-sonnet-4-6", "claude-opus-4-1", "claude-haiku-4-5"]
    base = config.resolved_base_url()
    if not base:
        return []
    key = config.openai_api_key or _os.environ.get("OPENAI_API_KEY")
    try:
        import httpx
        headers = {"User-Agent": "ronin"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        r = httpx.get(base.rstrip("/") + "/models", headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json().get("data", [])
        ids = [m.get("id", "") for m in data if m.get("id")]
        return sorted(ids)
    except Exception:  # noqa: BLE001
        return []


def split_leading_dir(user: str, root: Path | str) -> tuple[_Path | None, str]:
    """If a message *starts* with a path to an existing directory, return
    ``(resolved_dir, rest_of_message)`` so the session can switch into that
    folder — the way you'd ``cd`` into a repo before launching Claude Code.

    Returns ``(None, user)`` when there's no leading directory.
    """
    text = user.strip()
    if not text:
        return None, user
    tok = text.split()[0]
    cand = _Path(tok).expanduser()
    if not cand.is_absolute():
        cand = _Path(root) / tok
    try:
        if cand.is_dir():
            return cand.resolve(), text[len(tok):].strip()
    except OSError:
        return None, user
    return None, user


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _status_line(config: RoninConfig, result: "CodeRunResult", elapsed: float,
                 ledger: "object | None" = None) -> str:
    """A subtle per-turn footer: provider · model · ↑in ↓out · time, plus a
    Cost-Router savings line when routing is active."""
    u = result.usage or {}
    inp, out = u.get("input_tokens", 0), u.get("output_tokens", 0)
    cached = u.get("cache_read_input_tokens", 0)
    bits = [f"{config.provider} · {config.resolved_model()}"]
    if inp or out:
        tok = f"↑{_fmt_tokens(inp)} ↓{_fmt_tokens(out)}"
        if cached:
            tok += f" ⚡{_fmt_tokens(cached)} cached"
        bits.append(tok)
    bits.append(f"{elapsed:.1f}s")
    line = "  [#6b7089]" + "  ·  ".join(bits) + "[/#6b7089]"
    if ledger is not None and getattr(ledger, "turns", 0) > 0:
        line += f"\n  [#6b7089]💰 {ledger.summary()}[/#6b7089]"
    return line


def _render_diff(console: Console, diff: str) -> None:
    """Print a unified diff with proper syntax highlighting (Claude-Code style)."""
    if not diff.strip():
        return
    try:
        from rich.syntax import Syntax
        console.print(Syntax(diff, "diff", theme="monokai",
                             background_color="default", word_wrap=True))
        return
    except Exception:  # noqa: BLE001 - fall back to plain +/- coloring
        pass
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
            rel = args.get("path", "?")
            target = (root / rel)
            before = target.read_text(encoding="utf-8") if target.is_file() else ""
            after = before
            for e in args.get("edits", []):
                old, new = e.get("old_string", ""), e.get("new_string", "")
                if old and old in after:
                    after = after.replace(old, new, 1)
            edits_n = len(args.get("edits", []))
            console.print(f"  [yellow]›[/yellow] [bold]Edit[/bold] [cyan]{rel}[/cyan] "
                          f"[grey50]({edits_n} change(s))[/grey50]")
            _render_diff(console, unified_diff(rel, before, after))
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
    config: RoninConfig,
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
    # Headless callback overrides — when set (e.g. by the TUI), these replace the
    # console renderer/gate so the agent can be driven from another front-end.
    on_text_cb=None,
    on_step_cb=None,
    gate_cb=None,
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

    # Full-access mode lifts the filesystem sandbox (and auto-approve is set by
    # the session via yolo). Otherwise stay confined to the project root.
    _sandbox = not getattr(config, "full_access", False)
    tools = build_code_tools(root, undo_stack=undo_stack, sandbox=_sandbox)
    if read_only:
        # plan mode: explore but never mutate
        tools = [t for t in tools if t.name in _READONLY_CODE_TOOLS]
    # Semantic code intelligence (diagnostics / definition / references). These
    # are read-only, so they're available even in plan mode and to sub-agents.
    from .lsp import build_lsp_tools
    tools = tools + build_lsp_tools(root)
    # Web tools (web_search + fetch_url): read-only and safe, so the coding
    # agent gets them even in plan mode — look up docs/errors while it works.
    # Offline mode strips them below (NETWORK_TOOLS).
    from .web_tools import build_web_tools
    tools = tools + build_web_tools()
    # Git awareness (status/diff/log): read-only, so available in plan mode too.
    # Mutating git stays in the gated /commit & /pr commands, not here.
    from .git_tools import build_git_tools
    tools = tools + build_git_tools(root)
    # The live plan tracker: the agent maintains a checklist via update_todos.
    todo_store = TodoStore()
    tools = tools + [build_todo_tool(todo_store)]
    # Media: the agent can generate images into the project (free backend).
    if not read_only and include_image_tool:
        tools = tools + [build_image_tool(root)]
    # Extra tools (e.g. the unified agent's media + data tools).
    if extra_tools and not read_only:
        tools = tools + list(extra_tools)

    # Clarifying questions: only when there's a real human on the other end
    # (console set). Sub-agents and evals pass console=None and never block.
    if console is not None:
        tools = tools + [build_ask_user_tool(console)]
        # Bushido: let the agent persist a standing, cross-repo preference.
        from .bushido import build_bushido_tool
        tools = tools + build_bushido_tool()
        # Muscle memory: let the agent crystallize a solved workflow into a
        # reusable repo-local /skill (only when it can actually mutate the repo).
        if not read_only:
            from .muscle_memory import build_muscle_tool
            tools = tools + build_muscle_tool(root)

    # Offline mode: strip every network-touching tool so nothing can leave the
    # machine (the brain is already forced local in build_provider).
    if config.offline:
        from .offline import strip_network_tools
        tools = strip_network_tools(tools)

    # Dedup by name (first wins): extra_tools may overlap with built-ins (e.g.
    # web tools). Two tools with the same name confuse the model and some
    # providers reject the request outright.
    _seen: set[str] = set()
    tools = [t for t in tools if not (t.name in _seen or _seen.add(t.name))]

    # Project memory: fold RONIN.md / CLAUDE.md / AGENTS.md into the system
    # prompt so the agent follows the repo's conventions. Announce it once (on
    # the first turn of a session / a one-shot run), not on every turn.
    system = base_system or CODE_SYSTEM
    if extra_system:
        system += "\n\n" + extra_system
    # Bushido: the user's global code of honor, carried across every repo. Folded
    # in BEFORE project memory so a repo's own notes always override it.
    from .bushido import bushido_system_block
    _bushido = bushido_system_block()
    if _bushido is not None:
        system += _bushido
    mem = memory_system_block(root)
    if mem is not None:
        block, mem_name = mem
        system += block
        if console is not None and not history_prefix:
            console.print(f"[dim]📄 loaded project memory from [bold]{mem_name}[/bold][/dim]")

    # Context engineering: front-load the files most relevant to THIS request so
    # the model starts aimed at the right code (non-blocking; interactive turns
    # only — sub-agents/evals pass console=None and stay deterministic).
    if console is not None and getattr(config, "auto_context", False):
        from .context_engine import relevant_context
        _ctx = relevant_context(task, root)
        if _ctx:
            system += "\n\n" + _ctx
            console.print("[dim]📎 added relevant files to context[/dim]")

    provider = build_provider(config)
    # Surface rate-limit backoff so a (up to ~60s) retry wait doesn't look frozen.
    if console is not None:
        from .runner import attach_retry_notifier
        attach_retry_notifier(provider, console)
    # Compact old tool output before the context window fills. Claude has a 200k
    # window; most free/open models are far smaller (some 8–32k), so they'd error
    # long before a Claude-sized threshold — compact much earlier off-Anthropic.
    _compact_at = 120_000 if config.provider == "anthropic" else 28_000
    agent = ReActAgent(
        system=system,
        tools=tools,
        provider=provider,
        max_iterations=max_iterations,
        compact_after_tokens=_compact_at,
        compact_keep_recent=6,
    )

    before_tool = gate_cb or _selective_gate(console, yolo, _Path(root).resolve())

    # Stream the model's reasoning + summary live (the Claude-Code feel) when we
    # have a console; fall back to the step-narrator for non-interactive runs.
    # Headless callbacks (the TUI) take precedence over the console renderer.
    renderer = LiveRenderer(console) if console is not None else None
    on_step = on_step_cb or (renderer.on_step if renderer is not None else None)
    on_text = on_text_cb or (renderer.on_text if renderer is not None else None)

    # user-defined hooks (auto-format/test after edits, etc.) from .ronin/hooks.json
    from .hooks import build_after_tool, load_hooks
    _hooks = load_hooks(root)
    after_tool = build_after_tool(_hooks, root, console=console) if _hooks else None

    task = expand_file_mentions(task, root, offline=config.offline)  # inline @path / @url refs
    prompt = f"{history_prefix}\n\nCurrent request: {task}" if history_prefix else task
    if renderer is not None:
        renderer.start()  # soft "thinking…" spinner until the first token
    try:
        result = agent.run(prompt, on_step=on_step, before_tool=before_tool,
                           on_text=on_text, after_tool=after_tool)
    except KeyboardInterrupt:
        # Ctrl-C during a turn → stop THIS turn, keep the session alive.
        if renderer is not None:
            renderer.finish()
        return CodeRunResult(
            success=False,
            output="[#e0af68]⊘ interrupted — back to you.[/#e0af68]",
            iterations=0,
            error="interrupted",
            blocked=True,
        )
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


def build_ask_user_tool(console: "Console", *, ask_fn=None):
    """An 'ask_user' tool: let the agent ask the human a clarifying question
    BEFORE charging into an ambiguous task. Only wired up in interactive sessions
    (sub-agents and evals pass console=None, so they never block on input)."""
    from ronin_agent_patterns import Tool

    from .theme import ACCENT

    def _ask(question: str, options: list | None = None) -> str:
        console.print(f"\n[bold {ACCENT}]ʕ•ᴥ•ʔ asks:[/bold {ACCENT}] {question}")
        if options:
            for i, opt in enumerate(options, 1):
                console.print(f"  [{ACCENT}]{i}.[/{ACCENT}] {opt}")
        reader = ask_fn or input
        try:
            answer = reader("  ↳ your answer: ")
        except (EOFError, KeyboardInterrupt):
            return "(no answer — user is unavailable; proceed with your best judgment)"
        answer = (answer or "").strip()
        if not answer:
            return "(user gave no answer; proceed with your best judgment)"
        # let the user answer "2" to pick an option by number
        if options and answer.isdigit() and 1 <= int(answer) <= len(options):
            return str(options[int(answer) - 1])
        return answer

    return Tool(
        name="ask_user",
        description=(
            "Ask the user a clarifying question when the task is ambiguous — missing "
            "requirements, several valid interpretations, or a destructive/irreversible "
            "choice. Prefer asking ONE sharp question early over guessing wrong and "
            "redoing work. Don't ask about things you can determine yourself by reading "
            "the code. Args: question (string), options (optional list of choices the "
            "user can pick by number)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The clarifying question."},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional choices the user can select by number.",
                },
            },
            "required": ["question"],
        },
        handler=_ask,
    )


_SUBAGENT_SYSTEM = """You are a focused sub-agent spawned to handle ONE delegated
task. Work read-only: explore the codebase (read_file / list_files / search_files
/ glob) and reason. Do NOT attempt to edit or run anything. Return a concise,
self-contained result — the findings or answer the parent agent asked for, nothing
else. Be thorough but tight."""


def build_task_tool(config: RoninConfig, root: Path | str, *, max_iterations: int = 12):
    """A 'task' tool: delegate a sub-job to a read-only sub-agent (Claude Code's
    Task tool). The sub-agent explores and reports back; it can't mutate anything,
    and it has no 'task' tool of its own, so there's no runaway recursion."""
    from ronin_agent_patterns import Tool

    def _task(description: str, prompt: str) -> str:
        res = run_code_agent(
            config, prompt, root=root, console=None, yolo=True, read_only=True,
            max_iterations=max_iterations, base_system=_SUBAGENT_SYSTEM,
            include_image_tool=False,
        )
        if not res.success:
            return f"sub-agent error: {res.error or res.output}"
        return res.output or "(sub-agent returned no output)"

    return Tool(
        name="task",
        description=(
            "Delegate a focused sub-task to a read-only sub-agent — e.g. 'research how "
            "auth works across these files', 'find every place X is used', 'summarise "
            "this module'. The sub-agent explores the codebase and returns a concise "
            "result. Use it to parallelise or scope big multi-part jobs. Args: "
            "description (short label), prompt (the full instruction for the sub-agent)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Short label for the sub-task."},
                "prompt": {"type": "string", "description": "Full instruction for the sub-agent."},
            },
            "required": ["description", "prompt"],
        },
        handler=_task,
    )


def build_parallel_task_tool(config: RoninConfig, root: Path | str, *,
                             max_iterations: int = 12, max_workers: int = 4):
    """A 'parallel_task' tool: fan out SEVERAL read-only sub-agents at once and
    collect their results. Each sub-agent is the same read-only explorer as
    ``task``; running them concurrently turns an N-part investigation from N
    sequential round-trips into one. Read-only, so there's nothing to collide."""
    from concurrent.futures import ThreadPoolExecutor

    from ronin_agent_patterns import Tool

    def _run_one(item: dict) -> tuple[str, str]:
        desc = str(item.get("description", "task"))
        prompt = str(item.get("prompt", ""))
        res = run_code_agent(
            config, prompt, root=root, console=None, yolo=True, read_only=True,
            max_iterations=max_iterations, base_system=_SUBAGENT_SYSTEM,
            include_image_tool=False,
        )
        body = res.output if res.success else f"sub-agent error: {res.error or res.output}"
        return desc, body or "(no output)"

    def _parallel(tasks: list) -> str:
        if not tasks:
            return "no tasks provided — pass a list of {description, prompt}"
        with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as ex:
            results = list(ex.map(_run_one, tasks))  # ex.map preserves input order
        return "\n\n".join(f"### {desc}\n{body}" for desc, body in results)

    return Tool(
        name="parallel_task",
        description=(
            "Run SEVERAL read-only sub-agents concurrently and get all their results "
            "together — use when a job splits into independent investigations (e.g. "
            "'how does auth work', 'where is rate-limiting', 'list the API routes'). "
            "Much faster than calling `task` one at a time. Args: tasks (a list of "
            "{description, prompt} objects)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "Independent sub-tasks to run in parallel.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string", "description": "Short label."},
                            "prompt": {"type": "string", "description": "Full instruction for the sub-agent."},
                        },
                        "required": ["description", "prompt"],
                    },
                },
            },
            "required": ["tasks"],
        },
        handler=_parallel,
    )


_ISOLATED_SYSTEM = """You are a sub-agent working inside an ISOLATED git worktree —
your own private checkout of the repository. Make the change you were asked for:
read, edit, create files, and run commands freely. You are sandboxed — nothing you
do touches the main checkout or any other agent until the user reviews your diff.
When finished, briefly summarise what you changed and why. Keep the work tightly
scoped to your task."""


def build_isolated_task_tool(config: RoninConfig, root: Path | str, *,
                             max_iterations: int = 25, max_workers: int = 3):
    """An 'isolated_task' tool: run one or more MUTATING sub-agents, each in its
    own git worktree, in parallel. Because every agent edits a separate checkout,
    parallel writes can't collide; each returns a self-contained diff for the
    user to review and apply. Needs a git repo (worktrees are a git feature)."""
    from concurrent.futures import ThreadPoolExecutor

    from ronin_agent_patterns import Tool

    from .worktree import NotAGitRepo, git_worktree, is_git_repo, worktree_diff

    def _run_one(index: int, item: dict) -> tuple[str, str]:
        desc = str(item.get("description", f"task-{index}"))
        prompt = str(item.get("prompt", ""))
        try:
            with git_worktree(root, label=f"t{index}") as wt:
                res = run_code_agent(
                    config, prompt, root=wt, console=None, yolo=True, read_only=False,
                    max_iterations=max_iterations, base_system=_ISOLATED_SYSTEM,
                    include_image_tool=False,
                )
                diff = worktree_diff(wt)
        except NotAGitRepo as e:
            return desc, f"cannot isolate: {e}"
        summary = res.output if res.success else f"sub-agent error: {res.error or res.output}"
        diff_block = diff.strip() or "(no file changes)"
        return desc, f"{summary}\n\n--- diff (in isolated worktree) ---\n{diff_block}"

    def _isolated(tasks: list) -> str:
        if not is_git_repo(root):
            return ("isolated_task needs a git repository (run `git init` first). "
                    "For read-only work use task / parallel_task instead.")
        if not tasks:
            return "no tasks provided — pass a list of {description, prompt}"
        with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as ex:
            results = list(ex.map(lambda iv: _run_one(*iv), list(enumerate(tasks))))
        return "\n\n".join(f"### {desc}\n{body}" for desc, body in results)

    return Tool(
        name="isolated_task",
        description=(
            "Run one or more MUTATING sub-agents in parallel, each in its own isolated "
            "git worktree, so their edits can't collide. Each sub-agent can read, edit, "
            "and run code; it returns a summary plus a diff of its changes for you to "
            "review and apply. Use for parallel implementation work (e.g. 'add tests to "
            "module A' and 'refactor module B' at once). Requires a git repo. Args: tasks "
            "(a list of {description, prompt})."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "Independent mutating sub-tasks, each isolated in its own worktree.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string", "description": "Short label."},
                            "prompt": {"type": "string", "description": "Full instruction for the sub-agent."},
                        },
                        "required": ["description", "prompt"],
                    },
                },
            },
            "required": ["tasks"],
        },
        handler=_isolated,
    )


# In-session slash commands (the Claude-Code control surface). Both ``/cmd``
# and ``:cmd`` are accepted.
SLASH_COMMANDS: dict[str, str] = {
    "help": "show this help",
    "login": "set the LLM provider + API key (e.g. /login openrouter) — masked, saved locally",
    "clear": "forget the conversation so far",
    "undo": "revert the most recent file change",
    "diff": "show the working-tree git diff",
    "commit": "draft a commit message from the diff and commit it (gated)",
    "pr": "push the branch and open a PR (title/body drafted, gated)",
    "model": "show the model, or switch it: /model <name> (no key re-entry)",
    "models": "list the models available for the current provider",
    "route": "smart routing: /route <fast-model> <strong-model> (or /route off)",
    "verify": "self-verify mode: /verify on|off — after edits, the agent checks + fixes its own work",
    "voice": "speak your request: /voice [seconds] — records the mic and transcribes it",
    "memory": "show loaded project memory (RONIN.md / CLAUDE.md / AGENTS.md)",
    "init": "scaffold a RONIN.md project-memory file",
    "tools": "list the tools the agent can use",
    "mcp": "list connected MCP servers and their tools",
    "agents": "list the sub-agents ronin can delegate to",
    "compact": "summarize the conversation so far to free up context",
    "context": "show conversation size + a token estimate for this session",
    "copy": "copy ronin's last reply to the clipboard",
    "export": "write the conversation to a markdown file",
    "resume": "reload this project's saved session from disk",
    "vim": "toggle vi-style keybindings in the input box (/vim on|off)",
    "doctor": "health check: provider auth, model, services",
    "config": "show the active config (provider, model, paths)",
    "quit": "exit the session",
}


def _copy_to_clipboard(text: str) -> bool:
    """Best-effort copy to the OS clipboard. Returns True on success.
    Tries pbcopy (macOS), clip (Windows), then xclip/xsel (Linux)."""
    import shutil
    import subprocess
    candidates = (
        ["pbcopy"], ["clip"],
        ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"],
    )
    for cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=5)
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def _last_assistant_reply(transcript: list[str]) -> str | None:
    """The most recent 'ASSISTANT: …' entry, prefix stripped."""
    for entry in reversed(transcript):
        if entry.startswith("ASSISTANT: "):
            return entry[len("ASSISTANT: "):]
    return None


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — good enough for a context gauge."""
    return max(1, len(text) // 4) if text else 0


def _compact_transcript(config: "RoninConfig", transcript: list[str], console: "Console") -> None:
    """Summarize the conversation into a tight note and replace the transcript
    with it — frees context while keeping the gist (like Claude Code's /compact)."""
    if not transcript:
        console.print("[dim]nothing to compact yet[/dim]")
        return
    from ronin_agent_patterns import Message

    from .runner import build_provider
    convo = "\n".join(transcript)
    before = _estimate_tokens(convo)
    try:
        provider = build_provider(config)
        resp = provider.complete(
            system=("Summarize the conversation below into a tight set of bullet "
                    "points: decisions made, facts established, files/areas touched, "
                    "and any open threads. Preserve specifics (names, paths, numbers). "
                    "No preamble — just the summary."),
            messages=[Message(role="user", content=convo[:24000])],
            tools=[],
            max_tokens=1024,
        )
        summary = (resp.text or "").strip()
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]couldn't compact:[/yellow] {e}")
        return
    if not summary:
        console.print("[yellow]couldn't compact: empty summary[/yellow]")
        return
    transcript.clear()
    transcript.append(f"ASSISTANT: (summary of earlier conversation)\n{summary}")
    after = _estimate_tokens(summary)
    console.print(f"[green]✓[/green] compacted [dim]~{before} → ~{after} tokens[/dim]")


def _run_bash_inline(cmd: str, *, console: Console, root: Path | str,
                     transcript: list[str]) -> None:
    """``!cmd`` bash mode — run a shell command in the project dir, show its
    output, and record it so the agent has the result as context on the next
    turn (Claude Code's ``!`` prefix). The command is whatever the user typed
    in their own terminal — never anything from observed content."""
    import subprocess
    console.print(f"[{_MUTE}]$ {cmd}[/{_MUTE}]", highlight=False)
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=str(_Path(root).resolve()),
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        console.print(f"[{_ERR}]! timed out after 120s[/{_ERR}]")
        return
    except Exception as e:  # noqa: BLE001
        console.print(f"[{_ERR}]! {e}[/{_ERR}]")
        return
    out = ((proc.stdout or "") + (proc.stderr or "")).rstrip()
    if out:
        console.print(out, highlight=False)
    if proc.returncode != 0:
        console.print(f"[{_MUTE}](exit {proc.returncode})[/{_MUTE}]")
    transcript.append(f"USER: !{cmd}")
    body = out[:4000] if out else "(no output)"
    transcript.append(f"ASSISTANT: (ran `{cmd}` · exit {proc.returncode})\n{body}")


def _save_quick_note(note: str, *, console: Console, root: Path | str) -> None:
    """``#note`` quick-memory — file a durable one-liner into project memory
    (Claude Code's ``#`` shortcut). No agent round-trip."""
    from .project_memory import append_project_note
    if not note:
        console.print(f"[{_MUTE}]usage: #a fact worth remembering[/{_MUTE}]")
        return
    _path, name = append_project_note(root, note)
    console.print(f"[{_OK}]✓ saved to {name}[/{_OK}] [{_MUTE}]{note}[/{_MUTE}]",
                  highlight=False)


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
    config: RoninConfig,
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
    if cmd in ("login", "key"):
        # Switch provider + set its key from inside the session — masked input,
        # saved to local config, applied immediately. Usage: /login [provider] [model]
        from rich.prompt import Prompt

        from .config import PROVIDER_PRESETS, save_config
        old_provider = config.provider
        prov = parts[1].lower() if len(parts) > 1 else config.provider
        config.provider = prov
        if len(parts) > 2:
            config.model = parts[2]
        elif prov != old_provider:
            # Switching providers: drop the old provider's model so the new
            # provider's default applies (a Gemini login shouldn't keep a Qwen model).
            config.model = None
        known = ", ".join(PROVIDER_PRESETS)
        if prov not in PROVIDER_PRESETS and prov != "custom":
            console.print(f"  [yellow]unknown provider[/yellow] '{prov}' — known: {known}, custom")
            return "handled"
        if prov == "ollama":
            config.demo_mode = False
            path = save_config(config)
            console.print(f"  [green]✓[/green] provider → [bold]ollama[/bold] [dim](local, no key)[/dim] · {path}")
            return "handled"
        key = (Prompt.ask(f"  API key for [bold]{prov}[/bold] (input hidden — paste ONCE, then Enter)",
                          password=True, console=console) or "").strip()
        if not key:
            console.print("  [yellow]no key entered — nothing changed[/yellow]")
            return "handled"
        if key.count("sk-") >= 2 or len(key) > 400:
            console.print("  [red]✗ that looks pasted more than once — run [bold]/login[/bold] again and paste it once[/red]")
            return "handled"
        config.demo_mode = False
        config.set_key_for(prov, key)  # per-provider — never clobbers another provider's key
        path = save_config(config)
        console.print(f"  [green]✓[/green] [bold]{prov}[/bold] [dim]({config.resolved_model()})[/dim] "
                      f"— saved to [cyan]{path}[/cyan]; using it from your next message.")
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
    if cmd == "commit":
        from .git_helper import commit as _commit
        _commit(console, root, config)
        return "handled"
    if cmd == "pr":
        from .git_helper import open_pr
        open_pr(console, root, config)
        return "handled"
    if cmd == "model":
        if len(parts) > 1:  # /model <name> → switch model in place, no key re-entry
            from .config import save_config
            config.model = " ".join(parts[1:])
            save_config(config)
            console.print(f"  [green]✓[/green] model → [bold]{config.resolved_model()}[/bold] "
                          f"[dim](provider {config.provider})[/dim]; using it from your next message.")
            return "handled"
        console.print(
            f"[dim]provider[/dim] [bold]{config.provider}[/bold]  ·  "
            f"[dim]model[/dim] [bold]{config.resolved_model()}[/bold]"
        )
        console.print("[dim]switch with [bold]/model <name>[/bold] · list with [bold]/models[/bold][/dim]")
        return "handled"
    if cmd == "verify":
        from .config import save_config
        if len(parts) >= 2 and parts[1].lower() in ("on", "true", "yes"):
            config.verify = True
        elif len(parts) >= 2 and parts[1].lower() in ("off", "false", "no"):
            config.verify = False
        else:
            console.print(f"  [dim]self-verify is [bold]{'on' if config.verify else 'off'}[/bold] · "
                          "toggle with [bold]/verify on|off[/bold][/dim]")
            return "handled"
        save_config(config)
        console.print(f"  [green]✓[/green] self-verify [bold]{'on' if config.verify else 'off'}[/bold]"
                      + (" — the agent will review & fix its own work after edits." if config.verify else ""))
        return "handled"
    if cmd == "route":
        from .config import save_config
        if len(parts) >= 2 and parts[1].lower() in ("off", "none"):
            config.route_fast = config.route_strong = None
            save_config(config)
            console.print("  [green]✓[/green] routing off — using a single model.")
            return "handled"
        if len(parts) >= 3:
            config.route_fast, config.route_strong = parts[1], parts[2]
            save_config(config)
            console.print(f"  [green]✓[/green] routing on · simple→[bold]{parts[1]}[/bold] · "
                          f"complex→[bold]{parts[2]}[/bold]")
            return "handled"
        if config.route_fast and config.route_strong:
            console.print(f"  [dim]routing on · simple→[bold]{config.route_fast}[/bold] · "
                          f"complex→[bold]{config.route_strong}[/bold][/dim]")
        else:
            console.print("  [dim]routing off. Enable: [bold]/route <fast-model> <strong-model>[/bold] "
                          "(e.g. /route llama3.1-8b gpt-oss-120b)[/dim]")
        return "handled"
    if cmd == "models":
        names = _list_provider_models(config)
        if not names:
            console.print(f"[dim]couldn't list models for [bold]{config.provider}[/bold] "
                          f"(set a key with [bold]/login[/bold] first).[/dim]")
            return "handled"
        current = config.resolved_model()
        console.print(f"[bold]{config.provider}[/bold] models [dim]({len(names)})[/dim]")
        for n in names[:40]:
            mark = " [green]← active[/green]" if n == current else ""
            console.print(f"  [cyan]{n}[/cyan]{mark}")
        console.print("[dim]switch with [bold]/model <name>[/bold][/dim]")
        return "handled"
    if cmd == "memory":
        # persistent cross-session memory about the user
        from .memory_store import load_memories
        mems = load_memories()
        if mems:
            body = "\n".join(f"[#2dd4bf]•[/#2dd4bf] {m['text']}" for m in mems[-40:])
            console.print(Panel(body, title=f"🧠 remembers about you ({len(mems)})", border_style="#2dd4bf"))
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
        from .lsp import build_lsp_tools
        names = ([t.name for t in build_code_tools(root)]
                 + [t.name for t in build_lsp_tools(root)] + ["update_todos"])
        console.print("[dim]" + ", ".join(names) + "[/dim]")
        return "handled"
    if cmd == "mcp":
        from rich.table import Table as _Table

        from .mcp_client import _ACTIVE, load_mcp_servers
        configured = load_mcp_servers(root)
        live = {c.name: c for c in _ACTIVE}
        if not configured and not live:
            console.print("[dim]no MCP servers configured — add one with "
                          "[bold]ronin mcp add <name> <command>[/bold][/dim]")
            return "handled"
        grid = _Table.grid(padding=(0, 2))
        grid.add_column(style="cyan", no_wrap=True)
        grid.add_column()
        seen = set()
        for name, spec in configured.items():
            seen.add(name)
            c = live.get(name)
            if c is not None:
                status = f"[green]● connected[/green] [dim]· {len(c.tools)} tool(s)[/dim]"
            else:
                status = "[dim]configured (not started this session)[/dim]"
            grid.add_row(name, f"{status}  [dim]{spec.get('command', '')}[/dim]")
        for name, c in live.items():  # live but not in the config file (rare)
            if name not in seen:
                grid.add_row(name, f"[green]● connected[/green] [dim]· {len(c.tools)} tool(s)[/dim]")
        console.print("[bold]MCP servers[/bold]")
        console.print(grid)
        return "handled"
    if cmd == "agents":
        from rich.table import Table as _Table
        grid = _Table.grid(padding=(0, 2))
        grid.add_column(style="cyan", no_wrap=True)
        grid.add_column()
        grid.add_row("task", "read-only sub-agent — explores the codebase and reports "
                             "back; the agent spawns it to scope big multi-part jobs")
        console.print("[bold]sub-agents[/bold]")
        console.print(grid)
        console.print("[dim]the agent delegates to these on its own; you don't call them directly[/dim]")
        return "handled"
    if cmd == "copy":
        reply = _last_assistant_reply(transcript)
        if not reply:
            console.print("[dim]nothing to copy yet[/dim]")
        elif _copy_to_clipboard(reply):
            console.print(f"[green]✓[/green] copied last reply [dim]({len(reply)} chars)[/dim]")
        else:
            console.print("[yellow]no clipboard tool found[/yellow] [dim](install pbcopy/xclip/xsel)[/dim]")
        return "handled"
    if cmd == "resume":
        # /resume → list past sessions (a picker); /resume <n> → reload session n.
        from .sessions import list_sessions, load_session as _load_sess, set_current_session
        sess = list_sessions(root)
        if not sess:
            console.print("[dim]no past sessions for this project yet[/dim]")
            return "handled"
        if len(parts) > 1 and parts[1].isdigit():
            idx = int(parts[1]) - 1
            if not (0 <= idx < len(sess)):
                console.print(f"[yellow]no session #{parts[1]}[/yellow] — run [bold]/resume[/bold] to list")
                return "handled"
            chosen = sess[idx]
            transcript.clear()
            transcript.extend(_load_sess(chosen["id"]))
            set_current_session(chosen["id"])   # continue this one
            console.print(f"[green]✓[/green] resumed [dim]{chosen['turns']} turns · "
                          f"{chosen['title']!r}[/dim]", highlight=False)
            return "handled"
        console.print(f"[bold]past sessions[/bold] [dim]· {len(sess)} · this project[/dim]")
        for i, s in enumerate(sess[:20], 1):
            when = (s["updated"][:16].replace("T", " ")) or "?"
            console.print(f"  [cyan]{i:>2}[/cyan]  [#6b7089]{when}[/#6b7089]  "
                          f"{s['turns']:>2} turns  {s['title'][:58]}", highlight=False)
        console.print("[dim]reload one with [bold]/resume <number>[/bold][/dim]", highlight=False)
        return "handled"
    if cmd == "export":
        if not transcript:
            console.print("[dim]nothing to export yet[/dim]")
            return "handled"
        import datetime as _dt
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = _Path(root).resolve() / f"ronin-conversation-{stamp}.md"
        lines = ["# ronin conversation", ""]
        for e in transcript:
            if e.startswith("USER: "):
                lines += [f"## You", "", e[len("USER: "):], ""]
            elif e.startswith("ASSISTANT: "):
                lines += [f"## ronin", "", e[len("ASSISTANT: "):], ""]
            else:
                lines += [e, ""]
        try:
            out.write_text("\n".join(lines), encoding="utf-8")
            console.print(f"[green]✓[/green] exported → [cyan]{out}[/cyan]")
        except OSError as e:
            console.print(f"[red]export failed:[/red] {e}")
        return "handled"
    if cmd == "compact":
        _compact_transcript(config, transcript, console)
        return "handled"
    if cmd == "context":
        convo = "\n".join(transcript)
        turns = sum(1 for e in transcript if e.startswith("USER: "))
        toks = _estimate_tokens(convo)
        console.print(
            f"[bold]context[/bold]  [dim]·[/dim]  {turns} turns  [dim]·[/dim]  "
            f"{len(transcript)} entries  [dim]·[/dim]  ~{toks} tokens  [dim]·[/dim]  "
            f"~{len(convo)} chars",
            highlight=False,
        )
        console.print("[dim]shrink it with [bold]/compact[/bold] · wipe it with [bold]/clear[/bold][/dim]",
                      highlight=False)
        return "handled"
    if cmd == "vim":
        from .prompt_box import set_vi_mode
        want = None
        if len(parts) >= 2 and parts[1].lower() in ("on", "true", "yes"):
            want = True
        elif len(parts) >= 2 and parts[1].lower() in ("off", "false", "no"):
            want = False
        now_on = set_vi_mode(want)
        console.print(f"[green]✓[/green] vi keybindings [bold]{'on' if now_on else 'off'}[/bold]"
                      + (" [dim](Esc for normal mode)[/dim]" if now_on else ""))
        return "handled"
    if cmd in ("doctor", "config"):
        from rich.table import Table as _Table

        from . import __version__
        from .config import find_config_path
        path = find_config_path()
        grid = _Table.grid(padding=(0, 2))
        grid.add_column(style="cyan", no_wrap=True)
        grid.add_column()
        grid.add_row("config file", str(path) if path else "[red]none — run ronin init[/red]")
        grid.add_row("provider", config.provider)
        grid.add_row("model", config.resolved_model())
        if config.resolved_base_url():
            grid.add_row("base_url", config.resolved_base_url() or "")
        if cmd == "doctor":
            grid.add_row(f"{config.provider} key",
                         "[green]present[/green]" if config.has_provider_auth() else "[red]missing[/red]")
        services = config.configured_services()
        grid.add_row("services", ", ".join(services) if services else "[dim]none[/dim]")
        grid.add_row("ronin", __version__)
        console.print(grid)
        if cmd == "doctor":
            console.print("[dim]live key+model check: [bold]ronin doctor --check[/bold][/dim]")
        return "handled"

    console.print(f"[yellow]unknown command[/yellow] /{cmd} — try [cyan]/help[/cyan]")
    return "handled"


def run_code_session(
    config: RoninConfig,
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

    # Cost Router: when route_fast/route_strong are set, each turn runs on the
    # routed (possibly free) provider and we tally what that saved vs the strong
    # model. Off → ledger stays None and nothing changes.
    _routing_on = bool(config.route_fast and config.route_strong)
    ledger = None
    _rstats = None
    if _routing_on:
        from .cost import CostLedger
        from .router_stats import load_stats
        from .routing import baseline_for
        _bp, _bm = baseline_for(config)
        ledger = CostLedger(baseline_provider=_bp, baseline_model=_bm)
        _rstats = load_stats(root)   # self-tuning: learned per-blade reliability

    pending: list[str] = []  # messages typed while the agent was working
    while True:
        if pending:
            user = pending.pop(0).strip()
            console.print(f"[dim]▸ running queued message ›[/dim] {user}")
        else:
            try:
                user = read_prompt(
                    console,
                    hint="/ commands · @ files · ⇧⭾ mode · ⌃c stop · /q quit",
                    placeholder='Describe a change — / for commands, @ to add files',
                    status=config.provider,
                    root=root,
                ).strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye[/dim]")
                return
        if not user:
            continue
        # ! bash mode — run a shell command inline, no agent round-trip (CC's ! prefix)
        if user.startswith("!"):
            _bash = user[1:].strip()
            if _bash:
                _run_bash_inline(_bash, console=console, root=root, transcript=transcript)
            continue
        # ronin's OWN CLI command typed into chat (e.g. "ronin duel -a gemini") —
        # run the real command instead of letting the model hallucinate about it.
        _self_cmd = detect_self_command(user)
        if _self_cmd is not None:
            console.print(f"[#6b7089]↳ that's a ronin CLI command — running it[/#6b7089] "
                          f"[dim](type it with a leading ! to run any shell command)[/dim]")
            _run_bash_inline(_self_cmd, console=console, root=root, transcript=transcript)
            continue
        # # quick-memory — jot a durable note straight into project memory (CC's # shortcut)
        if user.startswith("#"):
            _save_quick_note(user[1:].strip(), console=console, root=root)
            continue
        # /voice [seconds] → record the mic, transcribe, run the transcript as the message
        if user.split()[0].lower() in ("/voice", "/v"):
            from .audio import listen
            secs, tok = 6.0, user.split()
            if len(tok) > 1:
                try:
                    secs = float(tok[1])
                except ValueError:
                    pass
            try:
                user = listen(config, seconds=secs, console=console)
            except Exception as e:  # noqa: BLE001
                console.print(f"[yellow]voice input failed:[/yellow] {e}")
                continue
            if not user:
                console.print("[dim]heard nothing — try again[/dim]")
                continue
            console.print(f"[#6b7089]heard:[/#6b7089] {user}")
        else:
            expanded = expand_custom_command(user, root)
            if expanded is not None:
                user = expanded  # custom /command → run its prompt template through the agent
            else:
                action = handle_slash_command(
                    user, console=console, root=root, config=config,
                    undo_stack=undo_stack, transcript=transcript,
                )
                if action == "exit":
                    return
                if action == "handled":
                    continue

        # Start a message with a folder path → switch into it (like cd'ing into a repo).
        new_root, rest = split_leading_dir(user, root)
        if new_root is not None:
            root = new_root
            console.print(f"  [#6b7089]→ now working in[/#6b7089] [bold]{root}[/bold]")
            if not rest:
                continue
            # Ground the agent in THIS directory so it doesn't drift to a
            # remembered project or a differently-scoped MCP tool.
            user = (f"(Your working directory is {root}. Use your file tools "
                    f"(list_files / read_file / search_files) here; ignore any other "
                    f"project paths mentioned earlier or in memory.)\n\n{rest}")

        history_prefix = ""
        if transcript:
            history_prefix = "Conversation so far:\n" + "\n".join(transcript[-6:])

        # Shift+Tab edit mode: plan → read-only, auto-accept → yolo, normal → default
        from .prompt_box import current_mode
        _mode = current_mode()
        _turn_yolo = True if _mode == "auto-accept" else yolo
        _read_only = (_mode == "plan")

        import time as _time
        _t0 = _time.time()
        # Capture anything typed WHILE the agent works, then run those as the
        # next turns — "send while it's working", like a managed-input UI.
        from .bg_processes import build_background_tools
        from .checkpoint import build_checkpoint_tools
        from .embeddings import build_semantic_tools
        from .input_queue import InputQueue
        from .vision_tools import build_vision_tools
        # Cost Router (+ self-tuning): route this turn to the fast/free or strong
        # blade, escalating if the cheap blade has proven unreliable in this repo.
        turn_cfg = config
        _decision = None
        if _routing_on:
            from .routing import route_turn_config
            turn_cfg, _decision = route_turn_config(config, user, stats=_rstats)
            if _decision is not None and _decision.escalated:
                console.print(f"[#6b7089]↑ self-tuning: escalated to "
                              f"{turn_cfg.provider} (cheap blade unreliable here)[/#6b7089]")
        _iq = InputQueue(console)
        with _iq:
            result = run_code_agent(
                turn_cfg, user, root=root, console=console, yolo=_turn_yolo,
                max_iterations=max_iterations, undo_stack=undo_stack,
                history_prefix=history_prefix, read_only=_read_only,
                extra_tools=(build_background_tools(root) + build_checkpoint_tools(root)
                             + build_vision_tools(turn_cfg, root)
                             + build_semantic_tools(turn_cfg, root)),
            )
        pending.extend(_iq.drain())
        _elapsed = _time.time() - _t0
        if ledger is not None:
            _u = result.usage or {}
            ledger.record(turn_cfg.provider, turn_cfg.resolved_model(),
                          _u.get("input_tokens", 0), _u.get("output_tokens", 0))
        if _rstats is not None and _decision is not None:
            from .router_stats import save_stats
            _rstats.record(_decision.tier, turn_cfg.provider, result.success)
            save_stats(root, _rstats)   # learn from this outcome
        transcript.append(f"USER: {user}")
        transcript.append(f"ASSISTANT: {result.output}")
        save_session(root, transcript)  # persist so `ronin code --continue` can resume
        if not result.success:
            console.print(f"\n{result.output}\n")   # clean error (e.g. rate-limit), session continues
        else:
            if not result.streamed:
                console.print(f"\n{result.output}")
            console.print(_status_line(turn_cfg, result, _elapsed, ledger=ledger) + "\n")


UNIFIED_SYSTEM = """You are ronin — one capable assistant living in the user's terminal.

REPLY FIRST: always answer the user; never return an empty response. For plain
questions or chit-chat ("hey", "what is groq"), just answer in plain text — no tools.

When the task touches the user's files or project, WORK LIKE A CAREFUL ENGINEER —
this is how to be reliable, the way Claude Code is:
1. EXPLORE before you conclude. Use list_files / glob / search_files to see what's
   actually there, then read_file the relevant files. NEVER guess a file's path or
   invent its contents — only discuss files you have actually listed or read.
2. STAY in the working directory you were given. Do not wander to other projects or
   paths from earlier turns or memory — if unsure, list the current directory first.
3. For "find bugs / review / explain" tasks: read the key files, then report CONCRETE
   findings — file · approximate line · the problem · a suggested fix. No vague
   generalities, and don't claim a bug in code you haven't read.
4. To change code, make focused edits with write_file / edit_file (shown for your
   approval); preserve style; then VERIFY with run_command (tests/lint) when possible.
5. Plan multi-step work with update_todos and keep exactly one item in_progress.
6. NEVER fake your way to green. If a command fails because a dependency isn't
   installed or the environment is off (e.g. "ModuleNotFoundError: loguru"), STOP
   and tell the user the real fix ("pip install loguru") — do NOT stub the import,
   add no-op shims, vendor a fake module, or weaken tests just to make them pass.
   Fix real bugs; report environmental problems.
Honor constraints literally — if told "don't change the code", only read and report.

Tools — pick the right one, don't confuse them ("write code to make an image" = WRITE
CODE, not generate_image):
- code: read_file / write_file / edit_file / multi_edit / glob / search_files / run_command
- media: generate_image / generate_video / speak   · web: web_search / fetch_url
- task: a read-only sub-agent for a focused sub-job   · the configured data tools (Stripe/Linear/…)
- remember: save a durable fact about the user

Keep replies tight. Generated media is shown to the user automatically."""


def run_unified_session(
    config: RoninConfig,
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
    from .memory_store import build_remember_tool, memory_prompt_block
    from .tools import build_tools

    undo_stack: list = []
    transcript: list[str] = load_session(root) if continue_session else []
    artifacts: list = []
    # media (image/video/speech) + data (stripe/linear/…) + persistent memory,
    # layered on the coding agent's machinery (streaming, diffs, gate, todos).
    from .mcp_client import build_mcp_tools

    media_tools = build_media_tools(artifacts, root=root)
    data_tools = build_tools(config)
    # console=None → load MCP tools silently (no "🔌 MCP fs · N tool(s)" chrome
    # at launch; Claude Code doesn't announce its tool wiring either).
    mcp_tools = build_mcp_tools(root, console=None)  # tools from .ronin/mcp.json servers
    from .bg_processes import build_background_tools
    from .checkpoint import build_checkpoint_tools
    from .embeddings import build_semantic_tools
    from .vision_tools import build_vision_tools
    # NB: web tools (web_search/fetch_url) are now built into the code agent
    # itself (run_code_agent), so they're intentionally NOT added here again.
    extra = (media_tools + data_tools + mcp_tools
             + build_background_tools(root) + build_checkpoint_tools(root)
             + build_vision_tools(config, root) + build_semantic_tools(config, root)
             + [build_task_tool(config, root),
                build_parallel_task_tool(config, root),
                build_isolated_task_tool(config, root),
                build_remember_tool()])
    # cross-session memory: what ronin remembers about the user (loaded into the
    # system prompt; we no longer print a "🧠 remembered" banner at launch).
    mem_block = memory_prompt_block()

    resumed = " · resumed" if (continue_session and transcript) else ""
    _welcome(console, config, root, yolo,
             title=f"ronin — one assistant for everything{resumed}",
             hint="talk · code · make images/video/voice · query data · @path · /help · /quit")

    # Cost Router: tally routed-turn savings vs the strong model (when routing on).
    _routing_on = bool(config.route_fast and config.route_strong)
    ledger = None
    _rstats = None
    if _routing_on:
        from .cost import CostLedger
        from .router_stats import load_stats
        from .routing import baseline_for
        _bp, _bm = baseline_for(config)
        ledger = CostLedger(baseline_provider=_bp, baseline_model=_bm)
        _rstats = load_stats(root)   # self-tuning: learned per-blade reliability

    pending: list[str] = []  # messages typed while the agent was working
    while True:
        if pending:
            user = pending.pop(0).strip()
            console.print(f"[dim]▸ running queued message ›[/dim] {user}")
        else:
            try:
                user = read_prompt(
                    console,
                    hint="/ commands · @ files · ⇧⭾ mode · ⌃c stop · /q quit",
                    placeholder='Try "build me a landing page" — / for commands, @ to add files',
                    status=config.provider,
                    root=root,
                ).strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye[/dim]")
                return
        if not user:
            continue
        # ! bash mode — run a shell command inline, no agent round-trip (CC's ! prefix)
        if user.startswith("!"):
            _bash = user[1:].strip()
            if _bash:
                _run_bash_inline(_bash, console=console, root=root, transcript=transcript)
            continue
        # ronin's OWN CLI command typed into chat (e.g. "ronin duel -a gemini") —
        # run the real command instead of letting the model hallucinate about it.
        _self_cmd = detect_self_command(user)
        if _self_cmd is not None:
            console.print(f"[#6b7089]↳ that's a ronin CLI command — running it[/#6b7089] "
                          f"[dim](type it with a leading ! to run any shell command)[/dim]")
            _run_bash_inline(_self_cmd, console=console, root=root, transcript=transcript)
            continue
        # # quick-memory — jot a durable note straight into project memory (CC's # shortcut)
        if user.startswith("#"):
            _save_quick_note(user[1:].strip(), console=console, root=root)
            continue
        # /voice [seconds] → record the mic, transcribe, run the transcript as the message
        if user.split()[0].lower() in ("/voice", "/v"):
            from .audio import listen
            secs, tok = 6.0, user.split()
            if len(tok) > 1:
                try:
                    secs = float(tok[1])
                except ValueError:
                    pass
            try:
                user = listen(config, seconds=secs, console=console)
            except Exception as e:  # noqa: BLE001
                console.print(f"[yellow]voice input failed:[/yellow] {e}")
                continue
            if not user:
                console.print("[dim]heard nothing — try again[/dim]")
                continue
            console.print(f"[#6b7089]heard:[/#6b7089] {user}")
        else:
            expanded = expand_custom_command(user, root)
            if expanded is not None:
                user = expanded  # custom /command → run its prompt template through the agent
            else:
                action = handle_slash_command(
                    user, console=console, root=root, config=config,
                    undo_stack=undo_stack, transcript=transcript,
                )
                if action == "exit":
                    return
                if action == "handled":
                    continue

        # Start a message with a folder path → switch into it (like cd'ing into a
        # repo). The agent's tools are then rooted in that project.
        new_root, rest = split_leading_dir(user, root)
        if new_root is not None:
            root = new_root
            console.print(f"  [#6b7089]→ now working in[/#6b7089] [bold]{root}[/bold]")
            if not rest:
                continue
            # Ground the agent in THIS directory so it doesn't drift to a
            # remembered project or a differently-scoped MCP tool.
            user = (f"(Your working directory is {root}. Use your file tools "
                    f"(list_files / read_file / search_files) here; ignore any other "
                    f"project paths mentioned earlier or in memory.)\n\n{rest}")

        history_prefix = ""
        if transcript:
            history_prefix = "Conversation so far:\n" + "\n".join(transcript[-6:])

        # Cost Router (+ self-tuning): route this turn to the fast/free or strong
        # blade (a different *provider*, not just a model), escalating an
        # unreliable cheap blade based on this repo's learned outcomes.
        from .routing import route_turn_config
        turn_cfg, _decision = route_turn_config(config, user, stats=_rstats)
        if _decision is not None and _decision.escalated:
            console.print(f"[#6b7089]↑ self-tuning: escalated to {turn_cfg.provider} "
                          f"(cheap blade unreliable here)[/#6b7089]")

        # Shift+Tab edit mode: plan → read-only, auto-accept → yolo, normal → default
        from .prompt_box import current_mode
        _mode = current_mode()
        _turn_yolo = True if _mode == "auto-accept" else yolo
        _read_only = (_mode == "plan")

        import time as _time
        _t0 = _time.time()
        # Capture anything typed WHILE the agent works → run it as the next turn.
        from .input_queue import InputQueue
        _iq = InputQueue(console)
        with _iq:
            result = run_code_agent(
                turn_cfg, user, root=root, console=console, yolo=_turn_yolo,
                max_iterations=max_iterations, undo_stack=undo_stack,
                history_prefix=history_prefix, extra_tools=extra, read_only=_read_only,
                base_system=UNIFIED_SYSTEM, extra_system=mem_block, include_image_tool=False,
            )
        pending.extend(_iq.drain())
        _elapsed = _time.time() - _t0

        # smarter agent: opt-in self-verification after a turn that made changes
        if config.verify and result.success and any(
                getattr(s, "kind", None) == "tool_call" for s in result.steps):
            console.print("[#6b7089]🔎 self-verifying…[/#6b7089]")
            _vprompt = (
                "Review your work on the request above. Inspect the current project state "
                "(read files / run tests as needed) and check it FULLY and CORRECTLY "
                "satisfies the request. If anything is missing, broken, or wrong, fix it "
                "now. If it is complete and correct, reply briefly with 'Verified.'")
            _vres = run_code_agent(
                turn_cfg, _vprompt, root=root, console=console, yolo=_turn_yolo,
                max_iterations=max_iterations, undo_stack=undo_stack,
                history_prefix=f"USER: {user}\nASSISTANT: {result.output}",
                extra_tools=extra, base_system=UNIFIED_SYSTEM, extra_system=mem_block,
                include_image_tool=False,
            )
            if _vres.success and _vres.output:
                result.output = f"{result.output}\n\n[verified] {_vres.output}"

        if ledger is not None:
            _u = result.usage or {}
            ledger.record(turn_cfg.provider, turn_cfg.resolved_model(),
                          _u.get("input_tokens", 0), _u.get("output_tokens", 0))
        if _rstats is not None and _decision is not None:
            from .router_stats import save_stats
            _rstats.record(_decision.tier, turn_cfg.provider, result.success)
            save_stats(root, _rstats)
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
        else:
            if not result.streamed:
                console.print(f"\n{result.output}")
            console.print(_status_line(turn_cfg, result, _elapsed, ledger=ledger) + "\n")
        show_artifacts(artifacts)  # display any image/video produced
