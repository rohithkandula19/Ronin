"""`ronin util telegram` — control ronin from your phone, outbound-only.

Extracted from main.py in the wedge cut (per-domain sub-app modules, the
relay.py pattern). Registers onto the shared `util` group.
"""
from __future__ import annotations

import os

import typer
from rich.console import Console

from .config import load_config
from .util_cmds import util_app

console = Console()

def _telegram_git_repos(root) -> list:
    """Bounded scan for git repos under root (skips heavy dirs, stops at a repo
    boundary, caps total)."""
    from pathlib import Path

    base = Path(root)
    skip = {
        "node_modules", ".venv", "venv", "__pycache__", ".cache", "Library",
        ".Trash", ".npm", ".cargo", "go", "dist", "build", ".git",
    }
    repos: list = []

    def walk(d, depth: int) -> None:
        if depth > 4 or len(repos) > 200:
            return
        if (d / ".git").exists():
            repos.append(d)
            return
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        for e in entries:
            if e.is_dir() and e.name not in skip and not e.name.startswith("."):
                walk(e, depth + 1)

    walk(base, 0)
    return repos


def _telegram_dirty_repos(root) -> list:
    """Git repos under root that currently have uncommitted/untracked changes."""
    import subprocess

    out = []
    for repo in _telegram_git_repos(root):
        try:
            st = subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain"],
                capture_output=True, text=True, timeout=20,
            )
            if st.returncode == 0 and st.stdout.strip():
                out.append(repo)
        except Exception:  # noqa: BLE001
            continue
    return out


# How often (seconds) the live status message may be edited. Telegram rate-limits
# editMessageText; coalescing to ~1/s keeps us well clear while still feeling live.
_TELEGRAM_PROGRESS_MIN_INTERVAL = 1.0


def _telegram_step_line(step) -> str | None:
    """Turn one agent Step into a short, human progress line, or None to skip.

    We only surface tool calls (the visible "doing something" moments). A
    tool_call step's content is {"name": <tool>, "input": <args>}. Examples:
    "reading src/app.py", "editing main.py", "ran: pytest -q",
    "searching for TODO". Unknown/uninteresting steps return None so the status
    line is not churned by thoughts or tool results.
    """
    if getattr(step, "kind", None) != "tool_call":
        return None
    content = getattr(step, "content", None) or {}
    if not isinstance(content, dict):
        return None
    name = content.get("name") or ""
    args = content.get("input") or {}
    if not isinstance(args, dict):
        args = {}

    def _short(value, limit: int = 80) -> str:
        s = str(value).strip().replace("\n", " ")
        return s if len(s) <= limit else s[: limit - 3] + "..."

    if name == "read_file":
        return f"reading {_short(args.get('path', ''))}"
    if name in ("write_file", "edit_file", "multi_edit"):
        return f"editing {_short(args.get('path', ''))}"
    if name == "run_command":
        return f"ran: {_short(args.get('command', ''))}"
    if name == "search_files":
        return f"searching for {_short(args.get('query', ''))}"
    if name == "glob":
        return f"globbing {_short(args.get('pattern', ''))}"
    if name == "list_files":
        return f"listing {_short(args.get('directory', '.'))}"
    if name == "update_todos":
        return "updating the plan"
    target = args.get("path") or args.get("command") or args.get("query") or ""
    return f"running {name} {_short(target)}".rstrip()


def _telegram_step_progress(progress, *, _now=None):
    """Build an on_step_cb that pushes throttled progress lines to `progress`.

    Coalesces: it edits the status message at most once every
    _TELEGRAM_PROGRESS_MIN_INTERVAL seconds (the FIRST step always shows so the
    user sees immediate life). Each shown line is prefixed with a small spinner
    frame so successive identical actions still visibly change (Telegram rejects
    an edit to identical text). Returns a callable(step) -> None.
    """
    import time

    now = _now or time.monotonic
    state = {"last": 0.0, "tick": 0}

    def on_step(step) -> None:
        line = _telegram_step_line(step)
        if not line:
            return
        t = now()
        # First line shows immediately; afterwards throttle to the interval.
        if state["last"] and (t - state["last"]) < _TELEGRAM_PROGRESS_MIN_INTERVAL:
            return
        state["last"] = t
        frame = "|/-\\"[state["tick"] % 4]
        state["tick"] += 1
        try:
            progress(f"{frame} {line}")
        except Exception:  # noqa: BLE001 - progress is best-effort, never fatal
            pass

    return on_step


def _telegram_changes_summary(root, *, max_chars: int = 2500) -> str:
    """Show what changed: per dirty repo, a `git diff --stat` plus a truncated
    diff, so the Telegram reply shows the actual edits (Claude-Code style)."""
    import subprocess

    parts = []
    for repo in _telegram_dirty_repos(root):
        try:
            stat = subprocess.run(
                ["git", "-C", str(repo), "diff", "--stat"],
                capture_output=True, text=True, timeout=20,
            ).stdout.strip()
            porcelain = subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain"],
                capture_output=True, text=True, timeout=20,
            ).stdout.strip()
            diff = subprocess.run(
                ["git", "-C", str(repo), "diff"],
                capture_output=True, text=True, timeout=20,
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            continue
        block = f"[{repo}]\n{stat or porcelain or '(changes)'}"
        if diff:
            block += "\n\n" + diff[:1500]
        parts.append(block)
    if not parts:
        return ""
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated; ask me to show the full diff)"
    return text


def _telegram_undo(root) -> str:
    """Best-effort /undo: stash uncommitted changes in dirty git repos under root
    so recent edits are reverted but recoverable via `git stash pop`."""
    import subprocess

    stashed: list[str] = []
    for repo in _telegram_dirty_repos(root):
        try:
            r = subprocess.run(
                ["git", "-C", str(repo), "stash", "push", "-u",
                 "-m", "ronin telegram undo"],
                capture_output=True, text=True, timeout=40,
            )
            if r.returncode == 0:
                stashed.append(str(repo))
        except Exception:  # noqa: BLE001
            continue
    if not stashed:
        return "Nothing to undo: no uncommitted changes found under the root."
    body = "\n".join(f"- {p}" for p in stashed)
    return (
        "Reverted recent changes (stashed) in:\n" + body
        + "\nRestore any of them with: git -C <repo> stash pop"
    )


@util_app.command()
def telegram(
    allow: list[int] = typer.Option(
        None, "--allow",
        help="Append a chat id to the allowlist for THIS run (repeatable). "
             "Merged with TELEGRAM_ALLOWED_CHAT_IDS and the config field.",
    ),
    once: bool = typer.Option(
        False, "--once",
        help="Do a single getUpdates poll and exit (useful for testing).",
    ),
    poll_timeout: int = typer.Option(
        30, "--poll-timeout",
        help="Long-poll timeout in seconds passed to getUpdates.",
    ),
) -> None:
    """Run Ronin from your phone over Telegram. Outbound-only and allowlisted.

    The bot long-polls api.telegram.org (no inbound port, works behind NAT) and,
    for messages from an ALLOWED chat id, runs a READ-ONLY file agent rooted at
    RONIN_TELEGRAM_ROOT (default: your home dir), then replies with the answer.
    It can read and search your files but never edits files or runs shell
    commands. Reads of obviously sensitive paths (~/.ssh, key/env files, ...) are
    refused.

    Set TELEGRAM_BOT_TOKEN (from @BotFather) and TELEGRAM_ALLOWED_CHAT_IDS
    (comma-separated chat ids). With an empty allowlist the bot answers nobody;
    it only tells a chat its numeric id so you can add it. See docs/TELEGRAM.md.
    """
    from .telegram_bot import (
        DEFAULT_MAX_ITERATIONS,
        ENV_ALLOWED,
        TelegramBot,
        TelegramConfigError,
        get_bot_token,
        is_secret_path,
        parse_allowed_chat_ids,
        redact_token,
        resolve_root,
    )

    # 1) Token: fail closed (exit non-zero, never poll) if missing/malformed.
    try:
        token = get_bot_token()
    except TelegramConfigError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)

    config = load_config()

    # 2) Allowlist: config field + env var + repeatable --allow flag.
    allowed = parse_allowed_chat_ids(
        os.environ.get(ENV_ALLOWED),
        config_ids=list(config.telegram_allowed_chat_ids or []),
    )
    for cid in (allow or []):
        if cid not in allowed:
            allowed.append(cid)

    # 3) Root the read-only file agent at RONIN_TELEGRAM_ROOT (default: home).
    #    Resolved once here so every message runs against one fixed, absolute root.
    root = resolve_root()

    # 4) The agent is the SAME read-only code-agent path consensus uses: it can
    #    read and search files under `root` but has NO write/edit/shell tool
    #    (read_only=True). `deny=is_secret_path` makes the read tools refuse or
    #    skip obviously sensitive paths so a stray request cannot dump a secret
    #    into the Telegram history. run_code_agent is imported LAZILY so importing
    #    this module stays light and offline.
    # Full mode (read + EDIT + run) is opt-in via RONIN_TELEGRAM_ALLOW_EDITS.
    # Unset/false => read_only (the safe default). The allowlist + secret guard
    # stay in force in both modes.
    edits_on = (
        os.environ.get("RONIN_TELEGRAM_ALLOW_EDITS", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )

    edit_nudge = (
        "You are operating over Telegram for the owner, who cannot see your screen. "
        "ACTUALLY DO the task end to end: read the relevant files, make the edits, and "
        "run any commands needed to verify it works (e.g. run the tests). Do not stop "
        "early and do not just describe what you would do. When finished, give a short, "
        "plain summary of exactly what you changed and what you ran."
    )

    def answer_fn(message_text: str, progress=None) -> str:
        # `progress(text)` (optional, passed by the bot) edits a live status
        # message in place so the work streams to the phone instead of arriving
        # as one final blob. We turn each agent Step into a short human line and
        # push it through progress(), THROTTLED so we never spam Telegram with an
        # edit per step (Telegram rate-limits edits, and most steps are noise).
        from .code_mode import run_code_agent
        from .memory_store import (
            build_forget_tool,
            build_recall_tool,
            build_remember_tool,
            relevant_prompt_block,
        )

        text = message_text.strip()
        if edits_on and text == "/undo":
            return _telegram_undo(root)

        on_step_cb = _telegram_step_progress(progress) if progress else None

        # AUTO-RECALL: load the durable facts relevant to THIS message and inject
        # them into the system prompt so the agent already knows the user (name,
        # stack, repos, preferences) and stops re-asking. Bounded (top-K + capped).
        # Memory failures must never break a reply.
        try:
            mem_block = relevant_prompt_block(message_text)
        except Exception:  # noqa: BLE001 - memory is best-effort, never fatal
            mem_block = ""

        # Only the EDIT mode (read_only False) gets the remember/recall/forget
        # tools, so the agent can update memory while it works. The default
        # read-only path passes NO extra_tools at all (a write-safety invariant the
        # bridge tests enforce); auto-recall still applies there via extra_system,
        # and explicit memory commands are handled deterministically by the bot.
        extra: dict = {}
        if edits_on:
            try:
                extra["extra_tools"] = [
                    build_remember_tool(), build_recall_tool(), build_forget_tool()]
            except Exception:  # noqa: BLE001 - best-effort
                pass

        res = run_code_agent(
            config,
            message_text,
            root=root,
            console=None,
            yolo=True,
            read_only=not edits_on,
            include_image_tool=False,
            max_iterations=40 if edits_on else DEFAULT_MAX_ITERATIONS,
            deny=is_secret_path,
            base_system=edit_nudge if edits_on else None,
            extra_system=mem_block,
            on_step_cb=on_step_cb,
            **extra,
        )
        out = res.output or res.error or "(no answer)"
        if edits_on:
            changes = _telegram_changes_summary(root)
            if changes:
                out = out + "\n\n=== what changed ===\n" + changes
        return out

    def briefing_fn(prompt: str) -> str:
        # A daily briefing runs its prompt through the SAME read-only agent at
        # fire time and sends the result. Read-only and secret-guarded, exactly
        # like a normal message; no progress streaming (it fires unattended).
        # AUTO-RECALL applies here too so a briefing reflects what we know.
        from .code_mode import run_code_agent
        from .memory_store import relevant_prompt_block

        try:
            mem_block = relevant_prompt_block(prompt)
        except Exception:  # noqa: BLE001 - memory is best-effort, never fatal
            mem_block = ""

        res = run_code_agent(
            config,
            prompt,
            root=root,
            console=None,
            yolo=True,
            read_only=True,
            include_image_tool=False,
            max_iterations=DEFAULT_MAX_ITERATIONS,
            deny=is_secret_path,
            extra_system=mem_block,
        )
        return res.output or res.error or "(no answer)"

    bot = TelegramBot(
        token=token,
        allowed_chat_ids=allowed,
        answer_fn=answer_fn,
        briefing_fn=briefing_fn,
        poll_timeout=poll_timeout,
    )

    # 4) getMe at startup so the user sees the bot is reachable.
    try:
        me = bot.get_me()
    except Exception as exc:  # noqa: BLE001
        # httpx errors embed the request URL, which carries the token. Redact it
        # so a startup getMe failure does not print the secret to the terminal.
        console.print(
            "[red]x[/red] could not reach Telegram (getMe failed): "
            f"{redact_token(str(exc), token)}"
        )
        raise typer.Exit(1)

    username = me.get("username", "?")
    console.print(f"[green]ok[/green] bot @{username} ready; allowed chats: {allowed}")
    if edits_on:
        console.print(
            f"[yellow]FULL mode[/yellow]: I can read, EDIT, and RUN commands in {root}. "
            "Send /undo to revert recent changes (stashed). Secret files stay blocked."
        )
    else:
        console.print(
            f"[dim]Read-only file access, rooted at {root}. I can read and search "
            "your files but cannot edit or run commands.[/dim]"
        )
    if not allowed:
        console.print(
            "[yellow]![/yellow] allowlist is empty: I will not run the agent for "
            f"anyone. Message the bot and it replies with your chat id; add it to "
            f"{ENV_ALLOWED} to enable me."
        )

    if once:
        bot.poll_once()
        return

    console.print("[dim]polling… press Ctrl+C to stop[/dim]")
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]stopped[/dim]")
