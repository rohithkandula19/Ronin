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


def startup_lines(config, root, *, version: str, mode: str = "code") -> list[str]:
    """The plain-text content lines of the startup card, from REAL runtime state.

    Pure (one cheap git call): identity, cost badge, provider/model, workspace,
    cwd, and the hint rows. Used by both the Rich and NO_COLOR renderers and by
    tests, so the card can't drift from reality."""
    from pathlib import Path

    from .status import cost_badge, git_state
    badge = cost_badge(config)[0]
    g = git_state(root)
    if g.repo:
        workspace = f"git:{g.label()}"
    else:
        workspace = "LOCAL WORKSPACE"
    ident = f"🐼 ronin v{version}"
    if mode and mode != "code":
        ident += f"  · {mode}"
    return [
        ident,
        f"{badge} · {config.provider}/{config.resolved_model()} · {workspace}",
        str(Path(root).resolve()),
        "",
        "@path adds files · @https:// reads pages · /help for commands",
        "/provider models · /free on $0 mode · /role agent roles",
    ]


def render_startup_card(console, config, root, *, version: str, mode: str = "code") -> None:
    """The premium compact product card shown at launch. Real state only; degrades
    to plain text under NO_COLOR and sheds the hint rows in a narrow terminal."""
    lines = startup_lines(config, root, version=version, mode=mode)
    width = _width(console)

    if no_color():
        console.print("\n".join(lines))
        return

    from rich.panel import Panel
    from rich.text import Text
    from .theme import gradient_text

    body = Text()
    # line 1 — gradient wordmark + version
    ident = lines[0]
    body.append_text(gradient_text("🐼 ronin"))
    body.append(ident.replace("🐼 ronin", "", 1), style=MUTE)
    body.append("\n")
    # line 2 — badge line: colour just the badge word
    badge_word = lines[1].split(" · ", 1)[0]
    badge_hex = {"FREE": OK, "LOCAL": ACCENT, "PAID": WARN, "UNKNOWN": MUTE}.get(badge_word, MUTE)
    body.append(badge_word, style=f"bold {badge_hex}")
    body.append(lines[1][len(badge_word):], style=SOFT)
    body.append("\n")
    body.append(lines[2], style=MUTE)   # cwd
    if width >= 56:  # hint rows only when there's room
        body.append("\n\n")
        body.append(lines[4], style=SOFT)
        body.append("\n")
        body.append(lines[5], style=SOFT)
    console.print()
    console.print(Panel(body, border_style=ACCENT, expand=False, padding=(0, 2)))


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
