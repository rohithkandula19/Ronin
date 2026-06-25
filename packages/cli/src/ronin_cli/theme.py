"""Shared visual theme for ronin's terminal UI — one place for colors, glyphs,
and how tool calls are labelled, so every surface looks consistent.

The palette is intentionally *soft*: muted pastels over hard ANSI colors, with a
signature cyan→teal gradient for the ronin wordmark."""
from __future__ import annotations

from typing import Any

# --- soft palette (hex pastels, not hard ANSI) -------------------------------
ACCENT = "#2dd4bf"          # teal — ronin brand
ACCENT2 = "#22d3ee"         # cyan
TOOL = "#7dd3fc"            # soft sky — tool activity (distinct from the teal accent)
OK = "#9ece6a"              # soft green
WARN = "#e0af68"            # soft amber
ERR = "#f7768e"             # soft rose
MUTE = "#6b7089"            # muted slate
SOFT = "#9aa0b8"            # soft foreground for secondary text
BULLET = f"[{ACCENT}]⏺[/{ACCENT}]"   # filled turn/action marker (Claude-Code style)
CONNECTOR = "⎿"                       # result connector under a tool call (CC's ⎿)

# One syntax-highlight theme for everything ronin renders (diffs, code blocks in
# answers) so the look is cohesive. Vivid, on-brand (purple/cyan). Mutable: the
# renderers (`code_mode`, `streaming`) re-import this at render time, so
# ``set_code_theme`` swaps the look live mid-session. Switch via /theme.
CODE_THEME = "dracula"

# Curated, on-brand-ish dark Pygments styles offered by /theme. Only names that
# actually ship with the installed Pygments are accepted (validated below).
_CURATED_THEMES = (
    "dracula", "monokai", "github-dark", "nord", "one-dark",
    "gruvbox-dark", "material", "native", "solarized-dark", "zenburn",
)


def available_code_themes() -> list[str]:
    """Curated themes that exist in the installed Pygments, in display order."""
    from pygments.styles import get_all_styles
    installed = set(get_all_styles())
    return [t for t in _CURATED_THEMES if t in installed]


def set_code_theme(name: str) -> bool:
    """Switch the live syntax-highlight theme. Returns False for an unknown name
    (leaving the current theme unchanged), so callers can warn rather than crash
    a later render on an invalid Pygments style."""
    global CODE_THEME
    from pygments.styles import get_all_styles
    if name not in set(get_all_styles()):
        return False
    CODE_THEME = name
    return True


def apply_saved_theme(name: str | None) -> None:
    """Apply a persisted theme preference at session start (no-op if None/unknown)."""
    if name:
        set_code_theme(name)

# gradient stops for the ronin wordmark (cyan → teal → mint)
GRADIENT = ((0x22, 0xD3, 0xEE), (0x2D, 0xD4, 0xBF), (0x34, 0xD3, 0x99))


def gradient_text(text: str, *, bold: bool = True):
    """Return a Rich Text with each character coloured along the ronin gradient
    — the soft, premium wordmark look."""
    from rich.text import Text

    stops = GRADIENT
    seg = max(len(text) - 1, 1)
    t = Text()
    for i, ch in enumerate(text):
        pos = i / seg * (len(stops) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(stops) - 1)
        f = pos - lo
        r = int(stops[lo][0] + (stops[hi][0] - stops[lo][0]) * f)
        g = int(stops[lo][1] + (stops[hi][1] - stops[lo][1]) * f)
        b = int(stops[lo][2] + (stops[hi][2] - stops[lo][2]) * f)
        style = f"#{r:02x}{g:02x}{b:02x}"
        if bold:
            style = "bold " + style
        t.append(ch, style=style)
    return t


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
