"""``csk code`` — the coding-agent surface (Claude Code / Cline shaped).

Give it a task. It reads your files, reasons, edits code, runs commands —
narrating each step. Every *write* and *shell command* is gated behind your
approval by default (read operations run freely). ``--yolo`` auto-approves
everything for trusted, sandboxed runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping

from rich.console import Console

from ronin_agent_patterns import ReActAgent, Step
from ronin_hardening import InjectionScanner

from pathlib import Path as _Path

from .code_tools import (
    SENSITIVE_TOOLS,
    build_code_tools,
    undo_last,
    ungated_mutators,
    unified_diff,
)
from .config import RoninConfig
from .media import build_image_tool
from .project_memory import load_project_memory, memory_system_block, write_memory_template
from .self_command import detect_self_command
from .sentinel import confidence_badge, is_low
from .prompt_box import read_prompt
from .runner import build_provider
from .streaming import LiveRenderer
from .theme import ACCENT as _ACCENT
from .theme import ERR as _ERR
from .theme import MUTE as _MUTE
from .theme import OK as _OK
from .todo import TodoStore, build_todo_tool


# Rotating launch tips — a different one greets you each session.
_WELCOME_TIPS = [
    "/help for commands · @ to add files · shift+tab to cycle mode",
    "@path drops a file in · @https://… pulls a web page in",
    "shift+tab cycles normal → auto-accept → plan",
    "type a request, approve the diff — or --yolo to auto-accept",
    "/model to switch models · /login to add a provider",
    "ronin dojo pits models against each other · ronin kaizen fixes its own code",
    "ronin nightshift works your backlog while you sleep",
    "ronin failover groq,gemini → never wait on a rate-limit again",
    "ronin --sentinel makes it abstain instead of bluffing",
    "# jots a note to memory · ! runs a shell command inline",
]


# Rotating input placeholders — a fresh suggestion in the prompt each turn.
_PLACEHOLDERS = [
    'Describe a change — / for commands, @ to add files',
    'Try "add a --json flag and update the tests"',
    'Try "explain @main.py and fix the bug in @utils.py"',
    'Try "build me a landing page"',
    'Try "add retry + backoff to the http client"',
    'Try "write tests for the parser module"',
    'Try "refactor this file into smaller functions"',
    '@path adds a file · @https://… adds a web page',
    'Try "find and fix the failing test"',
]


def _placeholder() -> str:
    # No ghost placeholder — a clean, empty input box (the `_PLACEHOLDERS` list is
    # kept in case we want to bring the rotating suggestions back).
    return ""


def _greeting() -> str:
    """A time-of-day greeting (best-effort; neutral fallback)."""
    try:
        import datetime
        h = datetime.datetime.now().hour
    except Exception:  # noqa: BLE001
        return "ready"
    return ("burning the midnight oil" if h < 5 else "good morning" if h < 12
            else "good afternoon" if h < 17 else "good evening" if h < 22 else "working late")


def _welcome(console: "Console", config: RoninConfig, root, yolo: bool, *, title: str, hint: str) -> None:
    """Launch welcome: the panda mascot animates inline beside the
    version/model/cwd block. The panda pose, greeting, and tip all **rotate each
    launch** so it feels alive — on a non-TTY (tests/pipes) it's deterministic so
    output stays stable."""
    import random

    from rich.text import Text

    from . import __version__, display_version
    from .panda_art import PANDA_ACTIVITIES, animate_inline
    from .theme import MUTE, SOFT, gradient_text

    is_tty = bool(getattr(console, "is_terminal", False))
    activity = random.choice(list(PANDA_ACTIVITIES)) if is_tty else "dancing"
    tip = random.choice(_WELCOME_TIPS) if is_tty else _WELCOME_TIPS[0]
    greeting = _greeting() if is_tty else "ready"

    info = Text()
    info.append_text(gradient_text("ronin"))   # cyan→teal→mint premium wordmark
    info.append(f" v{display_version(__version__)}", style=MUTE)
    info.append(f"  · {greeting}\n", style=f"italic {SOFT}")
    info.append(f"{config.provider} · {config.resolved_model()}\n", style=SOFT)
    info.append(str(_Path(root).resolve()), style=MUTE)
    if yolo:
        info.append("  · auto-approve (YOLO)", style="yellow")

    console.print()
    animate_inline(console, info, activity=activity, loops=3)
    console.print()
    console.print(Text(f"  💡 {tip}", style=MUTE))
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
    # Full structured conversation after this turn (Message list), for the session
    # to persist and feed back next turn. Empty when the turn errored/was blocked.
    messages: list = field(default_factory=list)


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
_READONLY_CODE_TOOLS = {"read_file", "list_files", "search_files", "glob", "project_memory_recall"}
# Fast mode keeps only the essential coding tools — the heavy extras (LSP, web,
# git, vision, semantic, background, checkpoint, …) are dropped so the per-call
# tool payload (and thus latency + token cost) is a fraction of the full set.
_FAST_TOOLS = {"read_file", "list_files", "search_files", "glob", "write_file",
               "edit_file", "multi_edit", "run_command", "update_todos"}
# Tools that are read-only / idempotent and thread-safe → a batch of them can run
# CONCURRENTLY (Claude-Code-style parallel tool use). Never includes writes,
# commands, or anything with side effects.
_PARALLEL_TOOLS = {"read_file", "list_files", "search_files", "glob",
                   "git_status", "git_diff", "git_log", "git_blame", "web_search", "fetch_url",
                   "semantic_search", "definition", "references", "diagnostics"}


def _session_path(root: Path | str) -> _Path:
    """Per-repo session file under .ronin/sessions/."""
    key = _hashlib.sha1(str(_Path(root).resolve()).encode()).hexdigest()[:12]
    return _Path(".ronin") / "sessions" / f"code-{key}.json"


def save_session(root: Path | str, transcript: list[str], *, messages: list | None = None) -> None:
    """Archive the FULL current-session transcript under .ronin/sessions/ — every
    session is kept as its own file (resumable via /resume), never overwritten.
    ``messages`` (the structured Message list) is persisted too, so ``--continue``
    restores real context, not just the flat text tail."""
    from .sessions import save_session as _archive
    _archive(root, transcript, messages=messages)
    # Opt-in, local-only SFT trace capture (RONIN_CAPTURE_TRACES=<dir>). Off by
    # default; never transmits; never raises into the session.
    from .trace_capture import maybe_capture
    maybe_capture(messages)


def load_session(root: Path | str) -> list[str]:
    """Reload the MOST RECENT session for this project and continue it (used by
    --continue); pick an older one with /resume. Returns its transcript."""
    from .sessions import latest_session, load_session as _load, set_current_session
    sid = latest_session(root)
    if not sid:
        return []
    set_current_session(sid)   # continue that session instead of forking a new file
    return _load(sid)


def _resume_message_history(continue_session: bool) -> list:
    """Structured Message history for a resumed session — real --continue, not the
    6-line flat text tail. Must be called after ``load_session(root)`` has set the
    current session. Empty for new sessions and legacy v1 files (those fall back
    to the transcript tail via history_prefix)."""
    if not continue_session:
        return []
    try:
        from ronin_agent_patterns.react import trim_to_complete_pairs

        from .sessions import current_session_id, load_session_messages
        return trim_to_complete_pairs(load_session_messages(current_session_id()))
    except Exception:  # noqa: BLE001 — a resume glitch must never block a new turn
        return []


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
    key = config.key_for(config.provider)
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


# Past-tense, samurai-panda-flavoured verbs for the retrospective footer line
# (Claude-Code's ``✻ Cogitated for 1m 35s`` look, ronin-styled).
_DONE_VERBS = ("Forged", "Sharpened", "Pondered", "Tracked", "Schemed",
               "Reasoned", "Hunted", "Plotted", "Weighed", "Cogitated")


def _status_line(config: RoninConfig, result: "CodeRunResult", elapsed: float,
                 ledger: "object | None" = None, budget: float | None = None,
                 root: "Path | str" = ".") -> str:
    """A subtle per-turn footer in Claude-Code's retrospective style —
    ``✻ <FREE/PAID> <verb> for <time> · ↑in ↓out · provider model · branch*`` —
    plus a Cost-Router savings line (routing) or a spend-vs-budget line (budget)."""
    import random

    from .status import cost_badge, git_state
    u = result.usage or {}
    inp, out = u.get("input_tokens", 0), u.get("output_tokens", 0)
    cached = u.get("cache_read_input_tokens", 0)
    verb = random.choice(_DONE_VERBS)
    bits = [f"{verb} for {elapsed:.1f}s"]
    if inp or out:
        tok = f"↑{_fmt_tokens(inp)} ↓{_fmt_tokens(out)}"
        if cached:
            tok += f" ⚡{_fmt_tokens(cached)} cached"
        bits.append(tok)
    bits.append(f"{config.provider} {config.resolved_model()}")
    badge, bhex = cost_badge(config)
    line = (f"  [#2dd4bf]✻[/#2dd4bf] [{bhex}]{badge}[/{bhex}]  [#6b7089]"
            + "  ·  ".join(bits) + "[/#6b7089]")
    g = git_state(root)
    if g.label():
        ghex = "#e0af68" if g.dirty else "#9ece6a"
        line += f"  [#6b7089]·[/#6b7089]  [{ghex}]{g.label()}[/{ghex}]"
    if ledger is not None and getattr(ledger, "turns", 0) > 0:
        if budget:
            over = ledger.spent >= budget
            colour = "#f7768e" if over else "#6b7089"
            line += (f"\n  [{colour}]💸 spent ${ledger.spent:.4f} of ${budget:.2f} budget"
                     f"{' — over!' if over else ''}[/{colour}]")
        else:
            line += f"\n  [#6b7089]💰 {ledger.summary()}[/#6b7089]"
    return line


def _render_diff(console: Console, diff: str, path: str | None = None) -> None:
    """Print a fixed-width, markup-safe unified-diff preview."""
    if not diff.strip():
        return
    from .streaming_diff import render_unified_diff

    for row in render_unified_diff(diff, path=path, width=console.width or 100):
        console.print(row)


def _is_floored_command(name: str, args: dict) -> bool:
    """True if a tool call is a catastrophic shell command that the destructive
    floor must gate even under ``--yolo`` / god-mode. Covers BOTH command tools
    (``run_command`` + ``run_background``). This is the single source of the
    floor decision, shared by the console gate and the front-end (gate_cb) gate
    so neither path can auto-approve a catastrophic command under yolo.
    """
    from .approvals import is_floored_tool_call
    return is_floored_tool_call(name, args)


def _floored_payload(args: dict) -> str:
    """The destructive string that actually tripped the floor, for the block card.

    It does not necessarily live under ``command``: an MCP/plugin tool can carry it
    as ``query`` / ``sql`` / ``script``. Showing the user the wrong (or an empty)
    string in the block card would make the refusal unreadable.
    """
    from .approvals import EXECUTABLE_ARG_KEYS, _payload_strings, is_destructive_command

    if not isinstance(args, dict):
        return ""
    for key, value in args.items():
        if str(key).strip().lower() in EXECUTABLE_ARG_KEYS:
            for payload in _payload_strings(value):
                if is_destructive_command(payload):
                    return payload
    return ""


def _high_risk_plugin_capabilities(
    name: str, capabilities_by_tool: Mapping[str, Iterable[str]] | None,
) -> tuple[str, ...]:
    """Declared plugin capabilities that require explicit confirmation."""
    if not capabilities_by_tool:
        return ()
    from .plugin_manifest import HIGH_RISK_CAPABILITIES

    values = capabilities_by_tool.get(name, ())
    return tuple(sorted({str(value) for value in values} & HIGH_RISK_CAPABILITIES))


def _approve_plugin_capability_floor(
    console: Console | None, name: str, capabilities: tuple[str, ...],
) -> "bool | str":
    """Require a block-level approval for a high-risk plugin capability."""
    if console is None:
        return ("blocked: plugin declares " + ", ".join(capabilities)
                + " capability; explicit interactive approval is required.")
    from .approvals import approve

    return approve({
        "kind": "api_call",
        "summary": f"run plugin tool '{name}' (declares {', '.join(capabilities)})",
        "reversible": False,
        "external": "payment" in capabilities,
        "details": {"capability_floor": list(capabilities)},
    }, console=console)


def _selective_gate(
    console: Console | None, yolo: bool, root: _Path,
    *, extra_gated: set[str] | None = None, rules: "PermissionRules | None" = None,
    capabilities_by_tool: Mapping[str, Iterable[str]] | None = None,
) -> Callable[[str, dict], "bool | str"]:
    """Auto-approve read tools; gate SENSITIVE_TOOLS (and ``extra_gated`` — the
    sensitive MCP/plugin tools) unless yolo.

    Consults persisted permission rules first (``.ronin/settings.json``): a
    standing **allow** runs un-prompted, a standing **deny** is refused with a
    reason. Otherwise it prompts ``[y]es · [a]lways · [n]o · or type why not``:
    **always** writes an allow-rule so the action sticks; any free-text answer
    becomes the denial reason handed back to the model (reject-with-feedback).

    For write_file / edit_file, renders a unified diff of the proposed change
    *before* asking — so you approve a visible diff, not a blind action.
    """
    from .permissions import load_rules
    gated = (extra_gated or set())
    _rules = rules if rules is not None else load_rules(root)

    def gate(name: str, args: dict) -> "bool | str":
        # Deny-rules are a KILL-SWITCH: checked for EVERY tool (even read-only
        # ones) and even under --yolo, so a committed `deny read_file *.env` or
        # `deny rm -rf*` actually hard-blocks. This must precede every short-circuit.
        deny = _rules.deny_reason(name, args)
        if deny is not None:
            return (f"blocked by a standing deny-rule ({deny!r}) in "
                    ".ronin/settings.json — do not retry; choose another approach.")
        # DESTRUCTIVE FLOOR — a catastrophic payload (rm -rf, force-push, drop
        # table, mkfs, fork bomb…) is NEVER silently auto-approved, not even under
        # --yolo / --god-mode. It always requires an explicit typed confirmation
        # (default-deny), with a block card + a safer alternative.
        #
        # This MUST precede the "not sensitive → run freely" short-circuit below.
        # It used to sit after it, which meant the floor was only ever reached by
        # tools that had already been CLASSIFIED sensitive: any tool outside that
        # set — an MCP server's `execute`, a plugin's own tool — skipped the floor
        # entirely, gate or no gate. A classification lookup is not allowed to
        # decide whether the outermost safety authority runs.
        if _is_floored_command(name, args):
            _cmd = str(args.get("command", "") or _floored_payload(args))
            if console is None:
                return ("blocked: destructive command refused — the safety floor "
                        "needs an interactive typed confirmation, unavailable here.")
            from .input_queue import pause_capture
            from .ui_cards import DESTRUCTIVE_CONFIRM_PHRASE, render_destructive_block
            render_destructive_block(console, _cmd, root)
            console.print(f"    [red]type [bold]{DESTRUCTIVE_CONFIRM_PHRASE}[/bold] to "
                          "proceed — anything else cancels:[/red] ", end="")
            try:
                with pause_capture():
                    _typed = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                return "blocked: destructive command cancelled (no confirmation)."
            if _typed != DESTRUCTIVE_CONFIRM_PHRASE:
                return ("blocked: destructive command not confirmed — pick a safer "
                        "approach (the safer alternative was shown above).")
            return True  # explicitly, deliberately confirmed
        high_risk = _high_risk_plugin_capabilities(name, capabilities_by_tool)
        if high_risk:
            return _approve_plugin_capability_floor(console, name, high_risk)
        if name not in SENSITIVE_TOOLS and name not in gated:
            return True  # reads + media generation run freely (never destructive:
            #              the floor above already had its say on every tool)
        if yolo:
            return True
        # A standing allow short-circuits the prompt (the cure for approval fatigue).
        if _rules.check(name, args) == "allow":
            return True
        if console is None:
            return False  # no way to ask → deny by default

        _after_content = None  # set for write paths → scanned for secrets below
        # The renderer already announced the tool (● Write(path)); here we just
        # show the diff / command and ask — no duplicate header.
        if name in ("write_file", "edit_file"):
            rel = args.get("path", "?")
            target = (root / rel)
            before = target.read_text(encoding="utf-8") if target.is_file() else ""
            if name == "write_file":
                after = args.get("content", "")
            else:  # edit_file
                old, new = args.get("old_string", ""), args.get("new_string", "")
                after = before.replace(old, new, 1) if old in before else before
            _after_content = after
            # Opt-in hunk-by-hunk review for whole-file writes (RONIN_HUNK_REVIEW):
            # approve/reject each chunk like `git add -p`. tc.arguments is the SAME
            # object the executor runs, so rewriting args["content"] makes the handler
            # apply exactly the approved subset. write_file only — edit_file derives
            # from old/new strings, so it falls through to the normal whole-edit gate.
            import os as _os
            if (_os.environ.get("RONIN_HUNK_REVIEW") and name == "write_file"
                    and after and before != after):
                from .hunk_review import review as _hunk_review
                from .input_queue import pause_capture
                with pause_capture():
                    _reviewed = _hunk_review(before, after, console=console)
                args["content"] = _reviewed          # → executor writes exactly this
                from .secret_guard import secret_warning
                _w = secret_warning(_reviewed)
                if _w:
                    console.print(_w)
                return True                           # the per-hunk y/n WAS the approval
            _render_diff(console, unified_diff(rel, before, after), path=rel)
        elif name == "run_command":
            # Premium approval card: Command · Directory · Risk. Rich escaping is
            # handled inside the card (Table cells don't parse the command as markup).
            from .ui_cards import render_shell_approval
            render_shell_approval(console, str(args.get("command", "")), root)
        elif name == "multi_edit":
            rel = args.get("path", "?")
            target = (root / rel)
            before = target.read_text(encoding="utf-8") if target.is_file() else ""
            after = before
            for e in args.get("edits", []):
                old, new = e.get("old_string", ""), e.get("new_string", "")
                if old and old in after:
                    after = after.replace(old, new, 1)
            _after_content = after
            _render_diff(console, unified_diff(rel, before, after), path=rel)
        else:
            from rich.markup import escape as _esc
            console.print(f"  [grey50]{_esc(str(args))}[/grey50]")

        # Secret-leak guard: warn loudly (don't block) if the new content looks
        # like it carries a live credential.
        if _after_content is not None:
            from .secret_guard import secret_warning
            _w = secret_warning(_after_content)
            if _w:
                console.print(_w)

        console.print("    [yellow]approve?[/yellow] [grey50]\\[y]es · \\[a]lways · "
                      "\\[n]o · or type why not[/grey50] ", end="")
        # Pause the type-ahead reader so its background thread doesn't steal the
        # approval keystroke (which would hang the prompt and replay 'y' as a turn).
        from .input_queue import pause_capture
        try:
            with pause_capture():
                raw = input().strip()
        except (EOFError, KeyboardInterrupt):
            return False
        low = raw.lower()
        if low in ("y", "yes"):
            return True
        if low in ("a", "always"):
            from rich.markup import escape as _esc

            from .permissions import add_allow_rule
            rule = _rules.rule_for(name, args, "allow")
            _rules.add(rule)            # short-circuit the rest of THIS session
            add_allow_rule(root, rule)  # persist to the user-global per-repo store
            # Be honest about scope: a "*" match (MCP/plugin/rewind tools, which
            # match on name) auto-approves ALL future calls of that tool, any args.
            scope = (f"[bold]all[/bold] {_esc(rule.tool)} calls (any arguments)"
                     if rule.match == "*" else f"{_esc(rule.tool)} {_esc(repr(rule.match))}")
            console.print(f"    [#6b7089]✓ always allowing[/#6b7089] [dim]{scope} "
                          f"— manage with /permissions[/dim]")
            return True
        if low in ("n", "no", ""):
            return False
        # Any other text is reject-with-feedback: hand the reason to the model so
        # it can adjust ("use pnpm, not npm") instead of just hitting a dead end.
        return raw

    return gate


def _user_prompt_injection_gate(task, *, console=None, gate_cb=None):
    """Trust polarity for F4: the user's OWN prompt is the TRUSTED channel, so a
    scanner hit is a warning — never a hard block (which locked out people who
    legitimately type injection strings, e.g. editing the injection tests). The
    untrusted channel (fetched web content) is scanned+enveloped in web_tools.

    Returns a blocked ``CodeRunResult`` only when an interactive user explicitly
    declines; otherwise ``None`` (proceed).
    """
    import sys as _sys
    scan = InjectionScanner().scan(task)
    if not scan.flagged:
        return None
    labels = [h["label"] for h in scan.hits]
    if console is not None:
        console.print(
            f"[#e0af68]\u26a0 your task matched prompt-injection patterns {labels}. "
            "You are the trusted user \u2014 this is a warning, not a block.[/#e0af68]"
        )
    if console is not None and gate_cb is None and _sys.stdin.isatty():
        try:
            resp = input("  proceed anyway? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            resp = "n"
        if resp not in ("y", "yes"):
            return CodeRunResult(
                success=False,
                output="[cancelled] injection-scan warning declined.",
                iterations=0,
                error="user declined after injection warning",
                blocked=True,
            )
    return None



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
    message_history: list | None = None,
    read_only: bool = False,
    extra_tools: list | None = None,
    extra_system: str = "",
    include_image_tool: bool = True,
    base_system: str | None = None,
    role: str | None = None,
    deny=None,
    # Headless callback overrides — when set (e.g. by the TUI), these replace the
    # console renderer/gate so the agent can be driven from another front-end.
    on_text_cb=None,
    on_reset_cb=None,
    on_step_cb=None,
    gate_cb=None,
) -> CodeRunResult:
    # Trust polarity: the user's OWN typed task is the TRUSTED channel, so a
    # scanner hit here is a warning, not a hard block (hard-blocking locked out
    # the exact people who legitimately type injection strings — e.g. someone
    # editing the injection tests — with no override). The untrusted channel
    # (fetched web pages / search results) is scanned + enveloped in web_tools.
    _blocked = _user_prompt_injection_gate(task, console=console, gate_cb=gate_cb)
    if _blocked is not None:
        return _blocked

    from .context_policy import resolve_context_policy

    context_policy = resolve_context_policy(config)

    # A read-only role (researcher / reviewer / architect) restricts the agent to
    # read-only tools — guidance that's also enforced, never a safety bypass.
    from .roles import role_is_read_only
    if role and role_is_read_only(role):
        read_only = True

    # Full-access mode lifts the filesystem sandbox (and auto-approve is set by
    # the session via yolo). Otherwise stay confined to the project root.
    _sandbox = not getattr(config, "full_access", False)
    try:
        from .constitution import load_constitution

        constitution = load_constitution(root)
    except ValueError as exc:
        return CodeRunResult(
            success=False, output=f"[blocked] invalid repository constitution: {exc}",
            iterations=0, error=str(exc), blocked=True,
        )
    tools = build_code_tools(
        root, undo_stack=undo_stack, sandbox=_sandbox, deny=deny,
        write_deny=lambda target: constitution.protects(target, root=root),
    )
    from .project_memory import build_project_memory_tools
    tools = tools + build_project_memory_tools(root)
    if read_only:
        # plan mode: explore but never mutate
        tools = [t for t in tools if t.name in _READONLY_CODE_TOOLS]
    # Semantic code intelligence (diagnostics / definition / references). These
    # are read-only, so they're available even in plan mode and to sub-agents.
    from .lsp import build_lsp_tools
    tools = tools + build_lsp_tools(root)
    # Semantic file retrieval stays available without credentials. Its fallback
    # is a deterministic local hashing index, so offline runs never send project
    # content to an embedding provider.
    from .embeddings import build_semantic_tools
    tools = tools + build_semantic_tools(config, root)
    # Web tools (web_search + fetch_url): read-only and safe, so the coding
    # agent gets them even in plan mode — look up docs/errors while it works.
    # Offline mode strips them below (NETWORK_TOOLS).
    from .web_tools import build_web_tools
    tools = tools + build_web_tools()
    # Optional web computer-use (Playwright). build_browser_tools() returns []
    # when Playwright is not installed, so this never breaks the toolbelt; the
    # extra simply stays absent until `pip install 'ronin-cli[browser]'`.
    # These touch the network, so offline mode strips them below (NETWORK_TOOLS).
    from .browser_tools import build_browser_tools
    tools = tools + build_browser_tools()
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

    # Fast mode: ship only the core coding tools so the per-call tool-schema
    # payload (the bulk of input tokens on a 20-tool agent) shrinks dramatically
    # — fewer tokens = faster turns + fewer rate-limit hits on free tiers.
    if getattr(config, "fast", False):
        tools = [t for t in tools if t.name in _FAST_TOOLS]

    # Project memory: fold RONIN.md / CLAUDE.md / AGENTS.md into the system
    # prompt so the agent follows the repo's conventions. Announce it once (on
    # the first turn of a session / a one-shot run), not on every turn.
    system = base_system or CODE_SYSTEM
    if extra_system:
        system += "\n\n" + extra_system
    # Role guidance shapes how the agent approaches the task (read-only roles are
    # additionally enforced above). It augments the system prompt; it never lifts
    # an approval gate.
    if role:
        from .roles import role_guidance
        _rg = role_guidance(role)
        if _rg:
            system += "\n\n" + _rg
    # Make the agent aware of ronin's own integration commands + what's connected,
    # so it recommends `ronin mcp install github` instead of a generic git tutorial.
    # Interactive turns only (console set) — sub-agents/evals stay deterministic.
    if console is not None:
        from .capabilities import capability_block
        system += "\n\n" + capability_block([t.name for t in tools])
    # Bushido: the user's global code of honor, carried across every repo. Folded
    # in BEFORE project memory so a repo's own notes always override it.
    from .bushido import bushido_system_block
    _bushido = bushido_system_block()
    if _bushido is not None:
        system += _bushido
    # Sentinel mode: license to abstain + a CONFIDENCE signal on every reply.
    if getattr(config, "sentinel", False):
        from .sentinel import SENTINEL_SYSTEM
        system += SENTINEL_SYSTEM
    mem = memory_system_block(root)
    if mem is not None:
        block, mem_name = mem
        system += block
        if console is not None and not history_prefix:
            console.print(f"[dim]📄 loaded project memory from [bold]{mem_name}[/bold][/dim]")

    # Context engineering: front-load the files most relevant to THIS request so
    # the model starts aimed at the right code (non-blocking; interactive turns
    # only — sub-agents/evals pass console=None and stay deterministic).
    # Auto-context front-loads relevant files — skipped in fast mode (leaner
    # context = faster turns; the agent can still read what it needs on demand).
    # Only on the FIRST turn (no structured history yet): injecting query-specific
    # files into `system` every turn would change the system block each time and
    # invalidate the prompt-cache prefix — defeating the point of persistent
    # history. Once history exists, the agent already carries what it read forward.
    if (console is not None and getattr(config, "auto_context", False)
            and not getattr(config, "fast", False) and not message_history):
        _ctx = ""
        # Scalable path: if the user built a repo index (`ronin index`), pull a
        # BUDGETED, ranked set of files from the persistent index — context stays
        # bounded even on a huge monorepo. Falls back to the in-memory map when
        # there's no index, so default behaviour is unchanged.
        try:
            from .repo_index import index_db_path, query_index
            _db = index_db_path(root)
            if _db.exists():
                _paths = query_index(task, _db, token_budget=context_policy.retrieval_budget_tokens)
                if _paths:
                    _ctx = ("## Relevant code (ronin repo index)\n"
                            + "\n".join(f"- {p}" for p in _paths))
        except Exception:  # noqa: BLE001 - index is best-effort; never break a run
            _ctx = ""
        if not _ctx:
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
    _fast = getattr(config, "fast", False)
    agent = ReActAgent(
        system=system,
        tools=tools,
        provider=provider,
        max_iterations=max_iterations,
        compact_after_tokens=context_policy.compaction_threshold_tokens,
        compact_keep_recent=4 if _fast else 6,
        # Fast mode caps each tool result tighter, so file dumps / command output
        # don't balloon the per-call payload.
        max_tool_result_chars=6000 if _fast else 16000,
    )

    # The set of tools that must pass the approval gate: the built-in mutators
    # plus any tool that flags itself sensitive=True (MCP writes, user plugins).
    # These used to bypass approval entirely. Read-only MCP tools stay out of it.
    _sensitive_names = set(SENSITIVE_TOOLS) | {t.name for t in tools if getattr(t, "sensitive", False)}
    _capabilities_by_tool = {
        t.name: tuple(getattr(t, "capabilities", ())) for t in tools
        if getattr(t, "capabilities", ())
    }
    # Fail-closed drift guard: a known-mutating tool that reached the toolbelt but
    # not the gate would bypass approval. Refuse rather than run ungated.
    _ungated = ungated_mutators({t.name for t in tools}, _sensitive_names)
    if _ungated:
        raise RuntimeError(
            f"refusing to run: mutating tool(s) {sorted(_ungated)} are not gated "
            "(tool-registry/gate drift) — add them to SENSITIVE_TOOLS or mark them sensitive."
        )
    _gate_root = _Path(root).resolve()
    if gate_cb is not None:
        # A front-end gate (TUI / headless) handles the human prompt, but the
        # sensitivity decision AND the standing permission rules live HERE so
        # MCP/plugin writes can't slip past a gate that only knew about the
        # built-in SENSITIVE_TOOLS, and so a deny-rule hard-blocks in every UI.
        from .permissions import load_rules as _load_rules
        _fe_rules = _load_rules(_gate_root)

        def before_tool(name: str, args: dict):
            # Deny is a kill-switch for every tool (see the console gate).
            deny = _fe_rules.deny_reason(name, args)
            if deny is not None:
                return (f"blocked by a standing deny-rule ({deny!r}) in "
                        ".ronin/settings.json — do not retry; choose another approach.")
            # DESTRUCTIVE FLOOR — a catastrophic payload is NEVER auto-approved by
            # --yolo/god-mode on the front-end (TUI/headless) path either; force it
            # to the human gate. Mirrors the console _selective_gate floor.
            #
            # Like that gate, this MUST precede the "not sensitive → True"
            # short-circuit: otherwise the floor is only reached by tools already
            # CLASSIFIED sensitive, so an MCP/plugin tool outside that set skips it
            # entirely. The floor is the outermost authority or it is not a floor.
            if _is_floored_command(name, args):
                return gate_cb(name, args)
            high_risk = _high_risk_plugin_capabilities(name, _capabilities_by_tool)
            if high_risk:
                # The front end owns the confirmation UI. This metadata is used
                # only for display by the callback, never passed to the handler.
                approval_args = dict(args)
                approval_args["__ronin_capability_floor"] = list(high_risk)
                return gate_cb(name, approval_args)
            if name not in _sensitive_names:
                return True
            if yolo:
                return True
            if _fe_rules.check(name, args) == "allow":
                return True
            return gate_cb(name, args)
    else:
        before_tool = _selective_gate(console, yolo, _gate_root,
                                      extra_gated=_sensitive_names,
                                      capabilities_by_tool=_capabilities_by_tool)

    # Stream the model's reasoning + summary live (the Claude-Code feel) when we
    # have a console; fall back to the step-narrator for non-interactive runs.
    # Headless callbacks (the TUI) take precedence over the console renderer.
    renderer = LiveRenderer(console, model=config.resolved_model()) if console is not None else None
    on_step = on_step_cb or (renderer.on_step if renderer is not None else None)
    on_text = on_text_cb or (renderer.on_text if renderer is not None else None)
    on_reset = on_reset_cb or (renderer.on_reset if renderer is not None else None)

    # user-defined hooks (auto-format/test after edits, etc.) from .ronin/hooks.json
    from .hooks import build_after_tool, load_hooks, untrusted_present
    _hooks = load_hooks(root)
    if not _hooks and untrusted_present(root):
        console.print("[#e0af68]⚠ untrusted .ronin/hooks.json — hooks NOT run "
                      "(each runs a shell command). Review it, then: "
                      "[bold]ronin hooks trust[/bold][/#e0af68]")
    after_tool = build_after_tool(_hooks, root, console=console) if _hooks else None

    # Faithfulness edit guard: score every proposed write/edit against the files
    # the agent actually read. Off by default; opt in via config faithfulness=
    # warn|gate (or --faithfulness on `csk code`, which sets it on the config).
    # ``warn`` surfaces an ungrounded-edit score; ``gate`` HOLDS an ungrounded
    # edit for the agent to revise, even under --yolo. Read-only / plan runs and
    # sub-agents (read_only=True) have no writes to guard, so it is a no-op there.
    from .code_faithfulness import EditGuard, wrap_after_tool, wrap_before_tool
    from .faithfulness_hook import mode_of as _faith_mode_of
    if not read_only:
        _guard = EditGuard(mode=_faith_mode_of(config), config=config)
        if _guard.active:
            after_tool = wrap_after_tool(after_tool, _guard)
            before_tool = wrap_before_tool(before_tool, _guard, console=console)

    task = expand_file_mentions(task, root, offline=config.offline)  # inline @path / @url refs
    # When we have real structured history (the REPL keeps it alive across turns),
    # seed the agent with the actual Message list and send just this turn's task —
    # no flattened text tail, which would double-count context and break the cache.
    # The text ``history_prefix`` is the fallback for callers without structured
    # history (one-shots, resumed sessions on their first turn).
    if message_history:
        prompt = task
    else:
        prompt = f"{history_prefix}\n\nCurrent request: {task}" if history_prefix else task
    if renderer is not None:
        renderer.start()  # soft "thinking…" spinner until the first token
    try:
        result = agent.run(prompt, history=message_history or None,
                           on_step=on_step, before_tool=before_tool,
                           on_text=on_text, on_reset=on_reset, after_tool=after_tool,
                           parallel_safe=lambda n: n in _PARALLEL_TOOLS)
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
    # Surface a failover so the user knows a different provider answered.
    _fell_to = getattr(provider, "failed_over_to", None)
    if _fell_to and console is not None:
        console.print(f"[#7dcfff]⚡ failed over to {_fell_to}[/#7dcfff] [dim](primary was unavailable)[/dim]")

    # Faithfulness-in-coding (Ronin's differentiator): a cheap, deterministic
    # post-answer grounding check. Flag any symbol/path the answer CLAIMS that
    # exists in nothing the agent read this turn (nor wrote, nor any file it
    # named that is really on disk). The flag rides along as a short note on the
    # output, so the Telegram bot and the console both surface it. ON by default
    # for code mode; opt out with RONIN_GROUNDING_CHECK=0. No model/network call.
    _output = result.output
    try:
        from .grounding_check import append_grounding_note, grounding_note
        _note = grounding_note(_output, result.trace, root=root, config=config)
        _output = append_grounding_note(_output, _note)
    except Exception:  # noqa: BLE001 - the grounding check must never break a run
        _output = result.output

    # Self-verify: after an edit task (not read-only, and only when the agent
    # actually changed code), detect + RUN this repo's test/verify command ONCE
    # and append a VERIFICATION verdict so the agent does not claim success
    # blindly. Reuses verify_cmd detection (pytest / npm test / cargo / ...).
    # ON by default for edit tasks; opt out with RONIN_SELF_VERIFY=0. Bounded
    # (one run, one timeout) and never raises - degrades to no note on any error.
    try:
        from .self_verify import append_verification_note, verification_note
        _v_note = verification_note(result.trace, root=root, config=config,
                                    read_only=read_only)
        _output = append_verification_note(_output, _v_note)
    except Exception:  # noqa: BLE001 - self-verify must never break a run
        pass
    return CodeRunResult(
        success=result.success,
        output=_output,
        iterations=result.iterations,
        steps=result.trace,
        usage=result.usage,
        error=result.error,
        streamed=bool(renderer and renderer.streamed_text),
        messages=result.messages,
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
    "provider": "show providers (free/paid + key health), or switch: /provider <name>",
    "free": "free-mode status, or switch to a $0 provider: /free [on]",
    "theme": "show or switch the code syntax-highlight theme: /theme [name]",
    "role": "set a coding role (researcher/implementer/reviewer/tester/architect/debugger): /role <name>",
    "mode": "show or set the edit mode: /mode normal|plan|auto-accept (same as Shift+Tab)",
    "plan": "enter plan (read-only) mode — explore without mutating",
    "clear": "forget the conversation so far",
    "undo": "revert the most recent file change",
    "diff": "show the working-tree git diff",
    "commit": "draft a commit message from the diff and commit it (gated)",
    "pr": "push the branch and open a PR (title/body drafted, gated)",
    "model": "show the model, or switch it: /model <name> (no key re-entry)",
    "models": "list the models available for the current provider",
    "route": "smart routing: /route <fast-model> <strong-model> (or /route off)",
    "verify": "self-verify mode: /verify on|off — after edits, the agent checks + fixes its own work",
    "effort": "reasoning budget: /effort low|medium|high|xhigh (or off) — maps to the provider's native knob",
    "voice": "speak your request: /voice [seconds] — records the mic and transcribes it",
    "memory": "show loaded project memory (RONIN.md / CLAUDE.md / AGENTS.md)",
    "init": "scaffold a RONIN.md project-memory file",
    "tools": "list the tools the agent can use",
    "mcp": "list connected MCP servers and their tools",
    "integrations": "show connected MCP servers + plugins, and how to add more",
    "agents": "list the sub-agents ronin can delegate to",
    "compact": "summarize the conversation so far to free up context",
    "context": "show conversation size + a token estimate for this session",
    "copy": "copy ronin's last reply to the clipboard",
    "export": "write the conversation to a markdown file",
    "resume": "reload this project's saved session from disk",
    "vim": "toggle vi-style keybindings in the input box (/vim on|off)",
    "doctor": "health check: provider auth, model, services",
    "config": "show the active config (provider, model, paths)",
    "cost": "show lifetime Cost-Router spend + savings",
    "router": "show routing + what the self-tuning router has learned",
    "status": "mission control — every ronin system in one view",
    "permissions": "show standing approval rules (/permissions clear to wipe)",
    "fix": "run the repo's tests; if red, repair to green",
    "quit": "exit the session",
}


# /help, grouped into product-feel sections for a calm, scannable layout. Every
# name here must be a REAL command in SLASH_COMMANDS (checked by tests).
_HELP_GROUPS: list[tuple[str, list[str]]] = [
    ("▸  start", ["help", "clear", "resume", "doctor", "status"]),
    ("🧠  models", ["login", "provider", "model", "models", "free", "route", "router", "cost"]),
    ("✏️  coding", ["mode", "plan", "diff", "undo", "commit", "pr", "fix"]),
    ("🚦  pipeline & roles", ["role", "verify", "effort"]),
    ("🔒  safety", ["permissions"]),
    ("🔌  integrations", ["mcp", "integrations", "tools", "agents", "voice"]),
    ("📁  memory & context", ["memory", "init", "context", "compact", "export", "copy"]),
    ("⚙️  session", ["theme", "vim", "config", "quit"]),
]


def _render_help(console: "Console") -> None:
    """A grouped, aligned, premium /help screen."""
    from rich.text import Text

    from .theme import ACCENT, MUTE, SOFT, gradient_text

    console.print()
    title = gradient_text("ronin")
    title.append("  ·  in-session commands", style=f"bold {SOFT}")
    console.print(Text("  ") + title)
    console.print()
    shown: set[str] = set()
    for header, names in _HELP_GROUPS:
        console.print(f"  [bold {ACCENT}]{header}[/bold {ACCENT}]")
        for name in names:
            desc = SLASH_COMMANDS.get(name)
            if not desc:
                continue
            shown.add(name)
            console.print(f"    [cyan]/{name:<9}[/cyan] [dim]{desc}[/dim]")
        console.print()
    # any command not placed in a group (future-proofing) still shows
    extra = [n for n in SLASH_COMMANDS if n not in shown]
    if extra:
        console.print(f"  [bold {ACCENT}]…more[/bold {ACCENT}]")
        for name in extra:
            console.print(f"    [cyan]/{name:<9}[/cyan] [dim]{SLASH_COMMANDS[name]}[/dim]")
        console.print()
    # Roles — the six coding roles, each with its one-line purpose.
    from .roles import ROLES, current_role
    _cur = current_role()
    console.print(f"  [bold {ACCENT}]🎭  roles[/bold {ACCENT}] [dim]· "
                  f"active: {_cur or 'none'} · [bold]/role <name>[/bold] · [bold]/role clear[/bold][/dim]")
    for r in ROLES.values():
        ro = "read-only" if r.read_only else "edits gated"
        console.print(f"    [cyan]/role {r.key:<11}[/cyan] [dim]{r.blurb:<22} ({ro})[/dim]")
    console.print()
    console.print(Text("  @path / @url to add context · ! to run a shell command · "
                       "# to note a memory · shift+tab to cycle mode", style=MUTE))
    console.print()


_TOOL_GROUPS: list[tuple[str, list[str]]] = [
    ("📖  read & search", ["read_file", "list_files", "search_files", "glob"]),
    ("✏️  edit", ["write_file", "edit_file", "multi_edit"]),
    ("🖥️  run", ["run_command"]),
    ("🔀  git", ["git_status", "git_diff", "git_log", "git_blame"]),
    ("🌐  web", ["web_search", "fetch_url"]),
    ("🧠  code intelligence", ["definition", "references", "diagnostics"]),
    ("📋  plan", ["update_todos"]),
]


def render_bar(used: int, total: int, width: int = 26) -> str:
    """A ▓░ fullness bar with a colour that warms as it fills. Pure (rich markup)."""
    frac = 0.0 if total <= 0 else max(0.0, min(1.0, used / total))
    fill = round(frac * width)
    colour = "#9ece6a" if frac < 0.6 else "#e0af68" if frac < 0.85 else "#f7768e"
    return f"[{colour}]{'▓' * fill}[/{colour}][#3b4261]{'░' * (width - fill)}[/#3b4261]"


def _render_tools(console: "Console", root) -> None:
    """A grouped, described view of the agent's tools (matches /help)."""
    from .git_tools import build_git_tools
    from .lsp import build_lsp_tools
    from .theme import ACCENT
    from .web_tools import build_web_tools

    by_name = {}
    for t in (build_code_tools(root) + build_lsp_tools(root) + build_web_tools()
              + build_git_tools(root) + [build_todo_tool(TodoStore())]):
        by_name[t.name] = t
    console.print(f"[bold]tools[/bold] [dim]({len(by_name)} available to the agent)[/dim]")
    shown: set[str] = set()
    for header, names in _TOOL_GROUPS:
        present = [n for n in names if n in by_name]
        if not present:
            continue
        console.print(f"  [bold {ACCENT}]{header}[/bold {ACCENT}]")
        for n in present:
            shown.add(n)
            desc = (by_name[n].description or "").replace("\n", " ").split(".")[0].strip()[:58]
            console.print(f"    [cyan]{n:<14}[/cyan] [dim]{desc}[/dim]")
    extra = [n for n in by_name if n not in shown]
    if extra:
        console.print(f"  [dim]+ {', '.join(extra)}[/dim]")


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


def _history_token_estimate(message_history: list) -> int:
    """Estimate tokens held in the structured history for the context gauge.

    ``message_history`` is a list of ``ronin_agent_patterns.Message`` objects
    (not dicts). Counting only ``dict`` items — as the pinned-bar gauge used to —
    always summed 0 and pinned "context left" at 100%. Mirror the estimator
    ``ReActAgent._maybe_compact`` uses: message content PLUS tool-call arguments
    (write_file/edit_file payloads live in ``tool_calls[].arguments``, not
    ``content``). Tolerates both Message objects and legacy dict messages.
    """
    import json as _json

    chars = 0
    for m in (message_history or []):
        if isinstance(m, dict):
            c = m.get("content", "")
            if isinstance(c, str):
                chars += len(c)
            elif isinstance(c, list):
                for p in c:
                    if isinstance(p, dict):
                        chars += len(p.get("text", "") or "")
            for tc in (m.get("tool_calls") or []):
                args = tc.get("arguments") if isinstance(tc, dict) else None
                chars += len(_json.dumps(args, default=str)) if args else 0
        else:
            chars += len(getattr(m, "content", "") or "")
            for tc in (getattr(m, "tool_calls", None) or []):
                args = getattr(tc, "arguments", None)
                chars += len(_json.dumps(args, default=str)) if args else 0
    return chars // 4


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
    from .code_tools import split_git_diff
    files = split_git_diff(diff)
    console.print(f"[#6b7089]{len(files)} file(s) changed[/#6b7089]")
    for path, chunk in files:
        console.print(f"\n  [bold #7dcfff]▸ {path or '(file)'}[/bold #7dcfff]")
        _render_diff(console, chunk[:6000], path=path)
        if len(chunk) > 6000:
            console.print("  [dim]…(file diff truncated)[/dim]")


def handle_slash_command(
    user: str,
    *,
    console: Console,
    root: Path | str,
    config: RoninConfig,
    undo_stack: list,
    transcript: list[str],
    message_history: list | None = None,
) -> str:
    """Dispatch an in-session command. Returns:
    ``"passthrough"`` (not a command → send to the agent),
    ``"handled"`` (command ran → loop again), or ``"exit"`` (quit the session).

    The per-command handlers live in ``slash_commands.py`` as a dispatch table;
    this stays a thin parser so it's easy to test and reason about.
    """
    from .slash_commands import SLASH_DISPATCH, SlashCtx

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
    handler = SLASH_DISPATCH.get(cmd)
    if handler is None:
        console.print(f"[yellow]unknown command[/yellow] /{cmd} — try [cyan]/help[/cyan]")
        return "handled"
    return handler(SlashCtx(parts=parts, console=console, root=root, config=config,
                            undo_stack=undo_stack, transcript=transcript,
                            message_history=message_history if message_history is not None else []))


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
    from .theme import apply_saved_theme
    apply_saved_theme(config.theme)  # honor a persisted /theme choice
    undo_stack: list = []
    _role_hints_shown: set[str] = set()  # surface each role suggestion at most once
    transcript: list[str] = load_session(root) if continue_session else []
    # Structured conversation kept alive across turns — the real Message list with
    # tool calls/results, so the agent remembers files it read and the prompt-cache
    # prefix stays warm. ``transcript`` (text) lives on for /resume + display; this
    # is what actually feeds the model. Starts empty (a resumed session falls back
    # to the text tail for its first turn, then accumulates structured history).
    message_history: list = _resume_message_history(continue_session)

    resumed = " · resumed" if (continue_session and transcript) else ""
    _welcome(console, config, root, yolo,
             title=f"ronin code{resumed}",
             hint="your request · @path to reference files · /help · /undo · /quit")

    # Cost Router: when route_fast/route_strong are set, each turn runs on the
    # routed (possibly free) provider and we tally what that saved vs the strong
    # model. Off → ledger stays None and nothing changes.
    _routing_on = bool(config.route_fast and config.route_strong)
    _budget = getattr(config, "budget", None)
    _track_cost = _routing_on or bool(_budget)   # cost ledger for routing OR budget
    ledger = None
    _rstats = None
    _budget_warned = False
    if _track_cost:
        from .cost import CostLedger
        from .routing import baseline_for
        _bp, _bm = baseline_for(config)
        ledger = CostLedger(baseline_provider=_bp, baseline_model=_bm)
    if _routing_on:
        from .router_stats import load_stats
        _rstats = load_stats(root)   # self-tuning: learned per-blade reliability

    pending: list[str] = []  # messages typed while the agent was working
    while True:
        if pending:
            user = pending.pop(0).strip()
            console.print(f"[dim]▸ running queued message ›[/dim] {user}")
        else:
            try:
                # Bottom-right gauge: provider + how much context window is left
                # (ticks down as the conversation grows, Claude-Code-style).
                _status = config.provider
                _used = 0
                _left = 100
                try:
                    # Count the structured history (Message objects, not dicts) —
                    # content + tool-call arguments — so the gauge actually ticks.
                    _used = _history_token_estimate(message_history)
                    _left = resolve_context_policy(config).remaining_percent(_used)
                    # Always-visible chip strip: [FREE] [provider:model] [mode]
                    # [branch*] [write-gated] [role:x], width-aware.
                    from .prompt_box import current_mode
                    from .roles import current_role
                    from .status import chip_strip
                    _status = chip_strip(config, root, edit_mode=current_mode(),
                                         role=current_role(), width=max(40, console.width))
                except Exception:  # noqa: BLE001
                    pass
                import os as _os_pin
                if _os_pin.environ.get("RONIN_PINNED", "").strip().lower() in {"1", "true", "yes", "on"}:
                    try:  # opt-in pinned-bottom input bar; falls back on ANY error
                        from pathlib import Path as _PinPath

                        from .prompt_pinned import pinned_prompt
                        user = pinned_prompt(
                            "› ", model=config.resolved_model(), ctx_pct=_left,
                            cwd=str(_PinPath(root).resolve()),
                        ).strip()
                    except (EOFError, KeyboardInterrupt):
                        raise
                    except Exception:  # noqa: BLE001 - never let the pinned bar break the REPL
                        user = read_prompt(
                            console, hint="/help for commands",
                            placeholder=_placeholder(), status=_status, root=root,
                        ).strip()
                else:
                    user = read_prompt(
                        console,
                        hint="/help for commands",
                        placeholder=_placeholder(),
                        status=_status,
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
                    message_history=message_history,
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
            # Switching projects: drop structured history so the agent doesn't carry
            # the OLD root's tool results (absolute paths, file contents) into the new
            # directory. Treat it like a soft /clear for the conversation context.
            message_history = []
            if not rest:
                continue
            # Ground the agent in THIS directory so it doesn't drift to a
            # remembered project or a differently-scoped MCP tool.
            user = (f"(Your working directory is {root}. Use your file tools "
                    f"(list_files / read_file / search_files) here; ignore any other "
                    f"project paths mentioned earlier or in memory.)\n\n{rest}")

        # Text fallback only when there's no structured history yet (first turn of
        # a resumed session). Once message_history is populated it carries context.
        history_prefix = ""
        if transcript and not message_history:
            history_prefix = "Conversation so far:\n" + "\n".join(transcript[-6:])

        # Shift+Tab edit mode: plan → read-only, auto-accept → yolo, normal → default
        from .prompt_box import current_mode
        from .roles import current_role, role_suggestion_line
        _mode = current_mode()
        _role = current_role()
        _turn_yolo = True if _mode == "auto-accept" else yolo
        _read_only = (_mode == "plan")
        # Gentle, once-per-session role suggestion (only when no role is set).
        _hint = role_suggestion_line(user, _role)
        if _hint and _hint not in _role_hints_shown:
            _role_hints_shown.add(_hint)
            console.print(f"  [#6b7089]{_hint}[/#6b7089]")

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
                history_prefix=history_prefix, message_history=message_history,
                read_only=_read_only, role=_role,
                extra_tools=(build_background_tools(root) + build_checkpoint_tools(root)
                             + build_vision_tools(turn_cfg, root)
                             + build_semantic_tools(turn_cfg, root)),
            )
        pending.extend(_iq.drain())

        # Escalation ladder: Sentinel flagged low confidence on a cheap blade →
        # retry the turn once on the strong blade instead of shipping a guess.
        if (getattr(config, "sentinel", False) and _routing_on
                and result.success and is_low(result.output)):
            from .routing import baseline_for
            from .runner import config_for_spec
            _sp, _sm = baseline_for(config)
            if (_sp, _sm) != (turn_cfg.provider, turn_cfg.resolved_model()):
                console.print("[#6b7089]▲ low confidence — escalating to the strong "
                              f"blade ({_sp})…[/#6b7089]")
                _strong = config_for_spec(config, {"provider": _sp, "model": _sm})
                _retry = run_code_agent(
                    _strong, user, root=root, console=console, yolo=_turn_yolo,
                    max_iterations=max_iterations, undo_stack=undo_stack,
                    history_prefix=history_prefix, message_history=message_history,
                    read_only=_read_only,
                    extra_tools=(build_background_tools(root) + build_checkpoint_tools(root)
                                 + build_vision_tools(_strong, root)
                                 + build_semantic_tools(_strong, root)),
                )
                if _retry.success:
                    result, turn_cfg = _retry, _strong

        _elapsed = _time.time() - _t0
        if ledger is not None:
            _u = result.usage or {}
            ledger.record(turn_cfg.provider, turn_cfg.resolved_model(),
                          _u.get("input_tokens", 0), _u.get("output_tokens", 0),
                          cached_tok=_u.get("cache_read_input_tokens", 0))
            if _routing_on:
                from .savings import append_turn
                append_turn(root, ledger.last_actual, ledger.last_baseline,
                            _decision.free if _decision is not None else False)
            if _budget and not _budget_warned and ledger.spent >= _budget:
                console.print(f"[bold #f7768e]💸 budget reached[/bold #f7768e] "
                              f"[dim]— spent ${ledger.spent:.4f} of ${_budget:.2f} this session[/dim]")
                _budget_warned = True
        if _rstats is not None and _decision is not None:
            from .router_stats import save_stats
            _rstats.record(_decision.tier, turn_cfg.provider, result.success)
            save_stats(root, _rstats)   # learn from this outcome
        transcript.append(f"USER: {user}")
        transcript.append(f"ASSISTANT: {result.output}")
        save_session(root, transcript, messages=message_history)
        # Adopt the structured conversation so the next turn keeps full context.
        # Adopt whenever messages are present — including a cap-hit turn
        # (success=False), whose react-trimmed messages are pairing-valid — so a
        # long task that exhausts the iteration cap doesn't start the next turn
        # cold. A plain error (rate-limit) returns no messages, so prior history
        # is kept intact.
        if result.messages:
            message_history = result.messages
        if not result.success:
            console.print(f"\n{result.output}\n")   # clean error (e.g. rate-limit), session continues
        else:
            if not result.streamed:
                console.print(f"\n{result.output}")
            if getattr(config, "sentinel", False):
                _badge = confidence_badge(result.output)
                if _badge:
                    console.print("  " + _badge)
            console.print(_status_line(turn_cfg, result, _elapsed, ledger=ledger, budget=_budget, root=root) + "\n")


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

ABOUT RONIN (when asked about ronin itself, answer from these facts — never guess
or claim ronin lacks a feature you're unsure of):
- ronin is a masterless, terminal-native, provider-agnostic coding agent
  (Claude-Code-style). It runs FREE on Gemini / Groq / Cerebras / OpenRouter /
  Ollama, or paid on Claude / OpenAI. The binary is `ronin` (alias `ro`).
- YES, ronin has games: `ronin play` opens a built-in arcade of 31 free terminal
  games (snake, tetris, wordle, 2048, sudoku, blackjack, minesweeper, and more);
  `ronin play <name>` launches one directly.
- Headline commands: bare `ronin` (this agent), `ronin consensus` (multi-model
  answer), `ronin map` (repo map), `ronin image` / `ronin video` (media), MCP tools.

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

    from .theme import apply_saved_theme
    apply_saved_theme(config.theme)  # honor a persisted /theme choice

    from .media import build_media_tools, show_artifacts
    from .memory_store import build_remember_tool, memory_prompt_block
    from .tools import build_tools

    undo_stack: list = []
    transcript: list[str] = load_session(root) if continue_session else []
    # Structured cross-turn conversation (see run_code_session for the rationale).
    message_history: list = _resume_message_history(continue_session)
    artifacts: list = []
    # media (image/video/speech) + data (stripe/linear/…) + persistent memory,
    # layered on the coding agent's machinery (streaming, diffs, gate, todos).
    from .mcp_client import build_mcp_tools

    media_tools = build_media_tools(artifacts, root=root)
    data_tools = build_tools(config, include_plugins=False)
    # console=None → load MCP tools silently (no "🔌 MCP fs · N tool(s)" chrome
    # at launch; Claude Code doesn't announce its tool wiring either).
    mcp_tools = build_mcp_tools(root, console=None)  # tools from .ronin/mcp.json servers
    from .plugins import build_plugin_tools
    # Pass the real console: build_plugin_tools prints ONLY on error, so this adds
    # no launch chrome — but an untrusted plugin (refused import, see plugin_trust)
    # must not fail silently, or the user's plugin just mysteriously stops working.
    plugin_tools = build_plugin_tools(root, console=console)  # user tools from .ronin/plugins/
    from .bg_processes import build_background_tools
    from .checkpoint import build_checkpoint_tools
    from .embeddings import build_semantic_tools
    from .vision_tools import build_vision_tools
    # NB: web tools (web_search/fetch_url) are now built into the code agent
    # itself (run_code_agent), so they're intentionally NOT added here again.
    extra = (media_tools + data_tools + mcp_tools + plugin_tools
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
    _budget = getattr(config, "budget", None)
    _track_cost = _routing_on or bool(_budget)   # cost ledger for routing OR budget
    ledger = None
    _rstats = None
    _budget_warned = False
    if _track_cost:
        from .cost import CostLedger
        from .routing import baseline_for
        _bp, _bm = baseline_for(config)
        ledger = CostLedger(baseline_provider=_bp, baseline_model=_bm)
    if _routing_on:
        from .router_stats import load_stats
        _rstats = load_stats(root)   # self-tuning: learned per-blade reliability

    pending: list[str] = []  # messages typed while the agent was working
    while True:
        if pending:
            user = pending.pop(0).strip()
            console.print(f"[dim]▸ running queued message ›[/dim] {user}")
        else:
            try:
                # Bottom-right gauge: provider + how much context window is left
                # (ticks down as the conversation grows, Claude-Code-style).
                _status = config.provider
                _used = 0
                _left = 100
                try:
                    # Count the structured history (Message objects, not dicts) —
                    # content + tool-call arguments — so the gauge actually ticks.
                    _used = _history_token_estimate(message_history)
                    _left = resolve_context_policy(config).remaining_percent(_used)
                    # Always-visible chip strip: [FREE] [provider:model] [mode]
                    # [branch*] [write-gated] [role:x], width-aware.
                    from .prompt_box import current_mode
                    from .roles import current_role
                    from .status import chip_strip
                    _status = chip_strip(config, root, edit_mode=current_mode(),
                                         role=current_role(), width=max(40, console.width))
                except Exception:  # noqa: BLE001
                    pass
                import os as _os_pin
                if _os_pin.environ.get("RONIN_PINNED", "").strip().lower() in {"1", "true", "yes", "on"}:
                    try:  # opt-in pinned-bottom input bar; falls back on ANY error
                        from pathlib import Path as _PinPath

                        from .prompt_pinned import pinned_prompt
                        user = pinned_prompt(
                            "› ", model=config.resolved_model(), ctx_pct=_left,
                            cwd=str(_PinPath(root).resolve()),
                        ).strip()
                    except (EOFError, KeyboardInterrupt):
                        raise
                    except Exception:  # noqa: BLE001 - never let the pinned bar break the REPL
                        user = read_prompt(
                            console, hint="/help for commands",
                            placeholder=_placeholder(), status=_status, root=root,
                        ).strip()
                else:
                    user = read_prompt(
                        console,
                        hint="/help for commands",
                        placeholder=_placeholder(),
                        status=_status,
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
                    message_history=message_history,
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
            # Switching projects: drop structured history so the agent doesn't carry
            # the OLD root's tool results (absolute paths, file contents) into the new
            # directory. Treat it like a soft /clear for the conversation context.
            message_history = []
            if not rest:
                continue
            # Ground the agent in THIS directory so it doesn't drift to a
            # remembered project or a differently-scoped MCP tool.
            user = (f"(Your working directory is {root}. Use your file tools "
                    f"(list_files / read_file / search_files) here; ignore any other "
                    f"project paths mentioned earlier or in memory.)\n\n{rest}")

        # Text fallback only when there's no structured history yet (first turn of
        # a resumed session). Once message_history is populated it carries context.
        history_prefix = ""
        if transcript and not message_history:
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
        from .roles import current_role
        _mode = current_mode()
        _role = current_role()
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
                history_prefix=history_prefix, message_history=message_history,
                extra_tools=extra, read_only=_read_only, role=_role,
                base_system=UNIFIED_SYSTEM, extra_system=mem_block, include_image_tool=False,
            )
        pending.extend(_iq.drain())
        _elapsed = _time.time() - _t0
        # Adopt structured history whenever present (incl. a cap-hit turn, whose
        # messages are pairing-valid) so the next turn keeps full context.
        if result.messages:
            message_history = result.messages

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
                message_history=message_history,
                extra_tools=extra, base_system=UNIFIED_SYSTEM, extra_system=mem_block,
                include_image_tool=False,
            )
            if _vres.success and _vres.output:
                result.output = f"{result.output}\n\n[verified] {_vres.output}"
                # NOTE: do NOT adopt _vres.messages into message_history. The verify
                # pass is seeded with a synthetic "Review your work…" user prompt;
                # threading its messages forward would inject that fake user turn
                # into every future request. Any fixes verify made live on disk and
                # the next real turn sees them through the file tools — so we keep
                # message_history at the main turn's clean history (set above).

        if ledger is not None:
            _u = result.usage or {}
            ledger.record(turn_cfg.provider, turn_cfg.resolved_model(),
                          _u.get("input_tokens", 0), _u.get("output_tokens", 0),
                          cached_tok=_u.get("cache_read_input_tokens", 0))
            if _routing_on:
                from .savings import append_turn
                append_turn(root, ledger.last_actual, ledger.last_baseline,
                            _decision.free if _decision is not None else False)
            if _budget and not _budget_warned and ledger.spent >= _budget:
                console.print(f"[bold #f7768e]💸 budget reached[/bold #f7768e] "
                              f"[dim]— spent ${ledger.spent:.4f} of ${_budget:.2f} this session[/dim]")
                _budget_warned = True
        if _rstats is not None and _decision is not None:
            from .router_stats import save_stats
            _rstats.record(_decision.tier, turn_cfg.provider, result.success)
            save_stats(root, _rstats)
        transcript.append(f"USER: {user}")
        transcript.append(f"ASSISTANT: {result.output}")
        save_session(root, transcript, messages=message_history)
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
            if getattr(config, "sentinel", False):
                _badge = confidence_badge(result.output)
                if _badge:
                    console.print("  " + _badge)
            console.print(_status_line(turn_cfg, result, _elapsed, ledger=ledger, budget=_budget, root=root) + "\n")
        show_artifacts(artifacts)  # display any image/video produced
