"""Shared visual theme for ronin's terminal UI — one place for colors, glyphs,
and how tool calls are labelled, so every surface looks consistent."""
from __future__ import annotations

from typing import Any

# --- palette -----------------------------------------------------------------
ACCENT = "magenta"          # ronin brand
ACCENT2 = "bright_magenta"
TOOL = "cyan"               # tool activity
OK = "green"
WARN = "yellow"
ERR = "red"
MUTE = "grey50"
BULLET = f"[{ACCENT}]●[/{ACCENT}]"   # Claude-Code-style action bullet


def short(value: Any, limit: int = 72) -> str:
    s = str(value).replace("\n", " ").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


# Map a tool call to a friendly "Verb(target)" label, like Claude Code's
# `● Read(file.py)` / `● Bash(npm test)`.
def tool_label(name: str, args: dict | None) -> tuple[str, str]:
    """Return (verb, target) for a tool call. ``verb`` is short and human;
    ``target`` is the key argument (path / command / prompt)."""
    a = args or {}
    table: dict[str, tuple[str, str]] = {
        "read_file": ("Read", a.get("path", "")),
        "list_files": ("List", a.get("directory", ".")),
        "search_files": ("Search", a.get("query", "")),
        "glob": ("Glob", a.get("pattern", "")),
        "write_file": ("Write", a.get("path", "")),
        "edit_file": ("Edit", a.get("path", "")),
        "multi_edit": ("Edit", a.get("path", "")),
        "run_command": ("Run", a.get("command", "")),
        "generate_image": ("Image", a.get("prompt", a.get("filename", ""))),
        "generate_video": ("Video", a.get("prompt", "")),
        "speak": ("Speak", a.get("text", "")),
    }
    if name in table:
        verb, target = table[name]
        return verb, short(target)
    # data tools (stripe_list_charges, linear_list_issues, …) and anything else
    pretty = name.replace("_", " ")
    return pretty[:1].upper() + pretty[1:], short(a) if a else ""
