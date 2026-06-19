"""Simon Says — repeat the panda's growing color sequence from memory."""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# The four playable colors and their render hues.
COLORS = ["R", "G", "B", "Y"]
HUES = {
    "R": "#f7768e",
    "G": "#9ece6a",
    "B": "#7dd3fc",
    "Y": "#e0af68",
}


def matches(sequence: list[str], reply: str) -> bool:
    """Pure rule: does ``reply`` (e.g. ``"rgby"``) equal the sequence in order?

    Case-insensitive. Any stray whitespace in the reply is ignored.
    """
    cleaned = "".join(reply.split()).upper()
    target = "".join(c.upper() for c in sequence)
    return cleaned == target


def _render(console: Console, sequence: list[str]) -> None:
    """Print the sequence as spaced, colored letters."""
    cells = [
        f"[bold {HUES.get(c.upper(), 'white')}] {c.upper()} "
        f"[/bold {HUES.get(c.upper(), 'white')}]"
        for c in sequence
    ]
    console.print("  " + "".join(cells))


def _play(console: Console) -> None:
    header(console, GAME)
    console.print(
        "  [dim]Watch the colors (R/G/B/Y), then type them back "
        "(e.g. rgby). (q to quit)[/dim]\n"
    )

    sequence: list[str] = []
    round_no = 0

    while True:
        round_no += 1
        sequence.append(random.choice(COLORS))

        console.print(f"  [dim]Round {round_no} — memorize:[/dim]")
        _render(console, sequence)
        console.print()

        reply = ask_line(console, "repeat:")
        low = reply.lower().strip()
        if low in ("q", "quit", ""):
            console.print(
                f"\n  [dim]Stopped at round {round_no}. Later![/dim]"
            )
            return

        if matches(sequence, reply):
            console.print(
                f"  [bold {HUES['G']}]✓ correct![/bold {HUES['G']}]\n"
            )
            continue

        score = round_no - 1
        console.print(
            f"  [bold {HUES['R']}]✗ wrong![/bold {HUES['R']}] "
            f"the sequence was [bold]"
            f"{''.join(sequence).upper()}[/bold]"
        )
        console.print(
            f"\n  [bold {HUES['Y']}]🎵 Game over — you reached round "
            f"{score}.[/bold {HUES['Y']}]"
        )
        return


GAME = GameMeta(key="simon", name="Simon Says", emoji="🎵",
                desc="repeat the growing color sequence", play=_play)
