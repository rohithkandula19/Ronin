"""Simon — watch the panda flash a growing color sequence, then repeat it."""
from __future__ import annotations

import random
import time

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# The four playable pads and their bright/dim render hues.
COLORS = ["R", "G", "B", "Y"]
NAMES = {"R": "RED", "G": "GREEN", "B": "BLUE", "Y": "YELLOW"}
HUES = {
    "R": "#f7768e",  # Red
    "G": "#9ece6a",  # Green
    "B": "#7dd3fc",  # Blue
    "Y": "#e0af68",  # Yellow
}
TEAL = "#2dd4bf"
MUTE = "#6b7089"

# A pad is three stacked block-rows; the middle row carries the letter.
_PAD_W = 9


def matches(sequence: list[str], reply: str) -> bool:
    """Pure rule: does ``reply`` (e.g. ``"rgby"``) equal the sequence in order?

    Case-insensitive. Any stray whitespace in the reply is ignored.
    """
    cleaned = "".join(reply.split()).upper()
    target = "".join(c.upper() for c in sequence)
    return cleaned == target


def _pad_lines(color: str, *, lit: bool) -> list[str]:
    """Three Rich-markup rows for one pad, bright/bold when ``lit`` else dim."""
    hue = HUES[color]
    label = NAMES[color]
    if lit:
        style = f"bold {hue} on {hue}"
        mid = f"bold black on {hue}"
    else:
        style = f"{MUTE}"
        mid = f"bold {MUTE}"
    bar = " " * _PAD_W
    name = label.center(_PAD_W)
    return [
        f"[{style}]{bar}[/{style}]",
        f"[{mid}]{name}[/{mid}]",
        f"[{style}]{bar}[/{style}]",
    ]


def _draw_board(console: Console, lit: str | None) -> None:
    """Render the four pads side by side; ``lit`` is the bright pad (or None)."""
    columns = [_pad_lines(c, lit=(c == lit)) for c in COLORS]
    for row in range(3):
        console.print("  " + "   ".join(col[row] for col in columns))


def _flash_sequence(console: Console, sequence: list[str], on: float,
                    off: float) -> None:
    """Light each pad in turn: bright for ``on`` s, then all-dim for ``off`` s."""
    for color in sequence:
        console.clear()
        console.print()
        console.print(f"  [{TEAL}]watch closely...[/{TEAL}]\n")
        _draw_board(console, lit=color)
        console.print(f"\n  [bold {HUES[color]}]{NAMES[color]}[/bold "
                      f"{HUES[color]}]")
        time.sleep(on)
        console.clear()
        console.print()
        console.print(f"  [{TEAL}]watch closely...[/{TEAL}]\n")
        _draw_board(console, lit=None)
        time.sleep(off)


def _play(console: Console) -> None:
    header(console, GAME)
    console.print(
        f"  [{MUTE}]The panda flashes a growing sequence of pads. After it "
        f"finishes,[/{MUTE}]"
    )
    console.print(
        f"  [{MUTE}]type the colors back by first letter "
        f"(e.g. [/{MUTE}][bold {HUES['R']}]r[/bold {HUES['R']}]"
        f"[bold {HUES['G']}]g[/bold {HUES['G']}]"
        f"[bold {HUES['B']}]b[/bold {HUES['B']}]"
        f"[bold {HUES['Y']}]y[/bold {HUES['Y']}][{MUTE}]). "
        f"q to quit.[/{MUTE}]\n"
    )
    _draw_board(console, lit=None)
    console.print()
    ask_line(console, "press Enter to start...")

    sequence: list[str] = []
    round_no = 0

    while True:
        round_no += 1
        sequence.append(random.choice(COLORS))

        # Speed up a touch each round, but stay playable.
        on = max(0.22, 0.5 - 0.025 * (round_no - 1))
        off = max(0.10, 0.2 - 0.010 * (round_no - 1))
        _flash_sequence(console, sequence, on, off)

        console.clear()
        console.print()
        console.print(
            f"  [bold {TEAL}]Round {round_no}[/bold {TEAL}]  "
            f"[{MUTE}]({len(sequence)} pads) — type the sequence:[/{MUTE}]\n"
        )
        _draw_board(console, lit=None)
        console.print()

        reply = ask_line(console, "repeat:")
        low = reply.lower().strip()
        if low in ("q", "quit"):
            console.print(
                f"\n  [{MUTE}]Stopped at round {round_no}. Later, babe![/{MUTE}]"
            )
            return

        # Tolerate junk: only R/G/B/Y letters count.
        if matches(sequence, reply):
            console.print(
                f"\n  [bold {HUES['G']}]correct![/bold {HUES['G']}] "
                f"[{MUTE}]get ready...[/{MUTE}]"
            )
            time.sleep(0.8)
            continue

        shown = " ".join(
            f"[bold {HUES[c]}]{NAMES[c]}[/bold {HUES[c]}]" for c in sequence
        )
        console.print(
            f"\n  [bold {HUES['R']}]wrong![/bold {HUES['R']}] "
            f"[{MUTE}]the sequence was:[/{MUTE}]"
        )
        console.print(f"  {shown}")
        score = round_no - 1
        console.print(
            f"\n  [bold {HUES['Y']}]Game over — you survived "
            f"{score} round{'s' if score != 1 else ''}.[/bold {HUES['Y']}]"
        )
        return


GAME = GameMeta(key="simon", name="Simon", emoji="🎵",
                desc="watch the flashing pads, then repeat the sequence",
                play=_play)
