"""The ronin boot banner — a big gradient RONIN wordmark with an animated
left-to-right reveal. The first thing you see; built to look like a beast."""
from __future__ import annotations

import sys
import time

from rich.console import Console
from rich.text import Text

from .theme import GRADIENT

# 6-row block letters
_F = {
    "R": ["█████ ", "█   █ ", "█████ ", "█ █   ", "█  █  ", "█   █ "],
    "O": [" ███  ", "█   █ ", "█   █ ", "█   █ ", "█   █ ", " ███  "],
    "N": ["█   █ ", "██  █ ", "█ █ █ ", "█ █ █ ", "█  ██ ", "█   █ "],
    "I": ["█████ ", "  █   ", "  █   ", "  █   ", "  █   ", "█████ "],
}
_WORD = "RONIN"
_ROWS = ["".join(_F[c][r] for c in _WORD) for r in range(6)]
_W = len(_ROWS[0])


def _col_color(x: int) -> str:
    pos = x / (_W - 1) * (len(GRADIENT) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(GRADIENT) - 1)
    f = pos - lo
    r = int(GRADIENT[lo][0] + (GRADIENT[hi][0] - GRADIENT[lo][0]) * f)
    g = int(GRADIENT[lo][1] + (GRADIENT[hi][1] - GRADIENT[lo][1]) * f)
    b = int(GRADIENT[lo][2] + (GRADIENT[hi][2] - GRADIENT[lo][2]) * f)
    return f"#{r:02x}{g:02x}{b:02x}"


def _banner_text(reveal: int) -> Text:
    """Render the banner with the first ``reveal`` columns lit (the wipe)."""
    t = Text(justify="left")
    for row in _ROWS:
        for x, ch in enumerate(row):
            if ch == "█" and x < reveal:
                t.append("█", style=_col_color(x))
            else:
                t.append(" ")
        t.append("\n")
    return t


def render_banner(console: Console, *, animate: bool = True) -> None:
    """Print the gradient RONIN banner — wiped in left-to-right on a real TTY."""
    if animate and getattr(console, "is_terminal", False) and sys.stdout.isatty():
        from rich.live import Live
        with Live(_banner_text(0), console=console, refresh_per_second=60, transient=False) as live:
            for col in range(0, _W + 1, 2):
                live.update(_banner_text(col))
                time.sleep(0.018)
            live.update(_banner_text(_W))
    else:
        console.print(_banner_text(_W))
    console.print(Text("  ʕ•ᴥ•ʔ  masterless Claude agent · one assistant for everything",
                       style=_col_color(_W // 2)))
