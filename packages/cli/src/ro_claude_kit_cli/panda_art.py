"""The big ronin panda — a detailed background-painted panda face with rosy
(magenta-themed) cheeks and a blink animation on launch."""
from __future__ import annotations

import sys
import time

from rich.console import Console

# pixel map: ' ' transparent · W white face · K black (ears/patches/nose) ·
# o pupil · m mouth · P magenta blush
_MAP = (
    "    KKKKKKK        KKKKKKK    ",
    "   KKKKKKKWWWWWWWWWWKKKKKKK   ",
    "   KKKKWWWWWWWWWWWWWWWWKKKK   ",
    "    KWWWWWWWWWWWWWWWWWWWWK    ",
    "    WWWWWWWWWWWWWWWWWWWWWW    ",
    "   WWWKKKKKKWWWWWWKKKKKKWWW   ",
    "  WWWKKKooKKKWWWWKKKooKKKWWW  ",
    "  WWWKKKKKKKKWWWWKKKKKKKKWWW  ",
    "  WWWWKKKKKKWWWWWWKKKKKKWWWW  ",
    "  WWWPPPWWWWWWWWWWWWWWPPPWWW  ",
    "  WWWPPPPWWWWKKKKWWWWPPPPWWW  ",
    "   WWPPPWWWWWWKKWWWWWWPPPWW   ",
    "    WWWWWWWWWmmmmWWWWWWWWW    ",
    "     WWWWWWWWWWWWWWWWWWWW     ",
    "       WWWWWWWWWWWWWWWW       ",
    "          WWWWWWWWWW          ",
)

_W = "on grey93"          # white face
_K = "on grey15"          # black ears / patches / nose
_P = "on #d27ec8"         # magenta blush
_PUP = "bold white on grey15"      # white pupil in a black patch
_MOUTH = "bold grey15 on grey93"   # dark smile on the white face


def _render(eyes: str) -> str:
    def run(ch: str, n: int) -> str:
        if ch == " ":
            return " " * n
        if ch == "W":
            return f"[{_W}]{' ' * n}[/]"
        if ch == "K":
            return f"[{_K}]{' ' * n}[/]"
        if ch == "P":
            return f"[{_P}]{' ' * n}[/]"
        if ch == "o":
            return f"[{_PUP}]{eyes * n}[/]"
        if ch == "m":
            return f"[{_MOUTH}]{'◡' * n}[/]"
        return " " * n

    out_lines = []
    for row in _MAP:
        spans, i = [], 0
        while i < len(row):
            j = i
            while j < len(row) and row[j] == row[i]:
                j += 1
            spans.append(run(row[i], j - i))
            i = j
        out_lines.append("".join(spans))
    return "\n".join(out_lines)


def render_panda(console: Console, *, animate: bool = True) -> None:
    """Print the big panda; blink a couple of times on a real TTY."""
    if animate and getattr(console, "is_terminal", False) and sys.stdout.isatty():
        from rich.live import Live
        frames = ["●", "●", "●", "–", "●", "●", "●", "–", "●"]  # open, open, …, blink
        with Live(_render("●"), console=console, refresh_per_second=12, transient=False) as live:
            for e in frames:
                live.update(_render(e))
                time.sleep(0.13)
            live.update(_render("●"))
    else:
        console.print(_render("●"))
