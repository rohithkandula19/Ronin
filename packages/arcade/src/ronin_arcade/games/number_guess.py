"""Hi-Lo number guessing — ronin picks 1..100, you home in on it."""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header


def feedback(secret: int, guess: int) -> str:
    """Pure rule: ``"higher"`` / ``"lower"`` / ``"correct"``."""
    if guess == secret:
        return "correct"
    return "higher" if guess > secret else "lower"


def _play(console: Console) -> None:
    header(console, GAME)
    secret = random.randint(1, 100)
    tries = 0
    console.print("  [dim]I'm thinking of a number between 1 and 100. (q to quit)[/dim]\n")
    while True:
        raw = ask_line(console, "your guess:")
        if raw.lower() in ("q", "quit", ""):
            console.print(f"\n  [dim]It was {secret}. Later![/dim]")
            return
        if not raw.lstrip("-").isdigit():
            console.print("  [yellow]numbers only[/yellow]")
            continue
        tries += 1
        verdict = feedback(secret, int(raw))
        if verdict == "correct":
            console.print(f"\n  [bold #9ece6a]🎉 Got it in {tries} tries![/bold #9ece6a]")
            return
        arrow = "↓ go lower" if verdict == "higher" else "↑ go higher"
        console.print(f"  [#7dd3fc]{arrow}[/#7dd3fc]")


GAME = GameMeta(key="guess", name="Number Guess", emoji="🔢",
                desc="Hi-Lo: find ronin's secret number", play=_play)
