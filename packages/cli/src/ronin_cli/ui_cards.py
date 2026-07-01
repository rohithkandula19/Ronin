"""Premium terminal cards — startup card, approval cards, destructive block.

Rendering is kept separate from gate/agent logic. All builders are pure and
NO_COLOR-aware: they degrade to clean plain text when colour is off or the
terminal is narrow. Nothing here bypasses a gate — the destructive card is a
*display*; the refuse/confirm decision lives in the caller.
"""
from __future__ import annotations

import os
import shutil

from .theme import ACCENT, ERR, MUTE, OK, SOFT, WARN

# the explicit phrase a user must type to clear the destructive floor
DESTRUCTIVE_CONFIRM_PHRASE = "run destructive"


def no_color() -> bool:
    """Honor the NO_COLOR convention (https://no-color.org)."""
    return bool(os.environ.get("NO_COLOR"))


def _width(console) -> int:
    w = getattr(console, "width", None)
    if not w:
        w = shutil.get_terminal_size((80, 24)).columns
    return max(20, w)


def safer_alternative(command: str) -> str:
    """A safer alternative for a destructive command, or "" if none obvious. Pure."""
    c = (command or "").lower()
    if "git push --force" in c or "git push -f" in c or "force-push" in c or "force push" in c:
        return "use git push --force-with-lease (refuses if someone else pushed)"
    if "rm -rf" in c or "rm -fr" in c:
        return "take a checkpoint first (ronin: --checkpoint), or move to trash instead of rm -rf"
    if "drop table" in c or "drop database" in c or "truncate table" in c or "delete from" in c:
        return "back up the table/db first, and scope the statement with a WHERE / LIMIT"
    if "mkfs" in c or "dd if=" in c or "format " in c:
        return "double-check the target device — this erases a disk; there is no undo"
    if "shred " in c or "deltree" in c or "del /f" in c:
        return "confirm the path; consider moving to trash so it's recoverable"
    return ""


def _plain_box(lines: list[tuple[str, str]], title: str) -> str:
    """Plain-text (NO_COLOR) box: 'Title\\n  key   value' rows, no ANSI."""
    out = [f"{title}"]
    keyw = max((len(k) for k, _ in lines), default=0)
    for k, v in lines:
        out.append(f"  {k.ljust(keyw)}  {v}" if k else f"  {v}")
    return "\n".join(out)


def _render_card(console, *, title: str, rows: list[tuple[str, str]],
                 border: str, title_style: str) -> None:
    """Render a titled key/value card (Rich Panel), or plain text under NO_COLOR."""
    if no_color():
        console.print(_plain_box(rows, title))
        return
    from rich.panel import Panel
    from rich.table import Table
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style=MUTE, no_wrap=True)
    grid.add_column(style=SOFT)
    for k, v in rows:
        grid.add_row(k, v)
    console.print(Panel(grid, title=f"[{title_style}]{title}[/{title_style}]",
                        title_align="left", border_style=border, expand=False))


def render_shell_approval(console, command: str, root, *, risk: str = "",
                          reason: str = "") -> None:
    """Approval card for a shell command (display only — the caller still asks)."""
    _render_card(
        console, title="Approval required: shell command", border=WARN, title_style=f"bold {WARN}",
        rows=[("Command", command), ("Directory", str(root)),
              ("Risk", risk or "runs a command"),
              *([("Reason", reason)] if reason else [])],
    )


def render_edit_approval(console, path: str, *, added: int, removed: int,
                         reason: str = "") -> None:
    """Approval card for a file edit (display only)."""
    _render_card(
        console, title="Approval required: file edit", border=WARN, title_style=f"bold {WARN}",
        rows=[("File", path), ("Change", f"+{added} -{removed}"),
              ("Risk", "workspace mutation"),
              *([("Reason", reason)] if reason else [])],
    )


def render_destructive_block(console, command: str, root) -> None:
    """The destructive-floor card: shown even under --yolo/--god-mode. It states
    what is blocked, why, and a safer alternative — then the caller requires a
    typed confirmation. This never auto-approves."""
    alt = safer_alternative(command)
    rows = [("Command", command), ("Directory", str(root)),
            ("Risk", "IRREVERSIBLE — data loss / force-push / disk wipe"),
            ("Blocked", "the destructive floor never auto-approves this, even in --god-mode")]
    if alt:
        rows.append(("Safer", alt))
    _render_card(console, title="⛔ Destructive command — blocked by the safety floor",
                 border=ERR, title_style=f"bold {ERR}", rows=rows)
