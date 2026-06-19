"""Pandle — guess the hidden 5-letter word in 6 tries, ronin's take on Wordle."""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# A small built-in dictionary of common 5-letter words.
WORDS = [
    "apple", "brave", "crane", "drive", "eagle", "flame", "grape", "house",
    "input", "joker", "knife", "lemon", "money", "night", "ocean", "plant",
    "queen", "river", "stone", "table", "ultra", "vivid", "water", "xenon",
    "youth", "zebra", "bread", "chair", "dream", "earth", "frost", "ghost",
    "happy", "ivory", "light", "music", "noble", "olive", "pride", "quiet",
]

# Render colors.
GREEN = "#9ece6a"
YELLOW = "#e0af68"
GREY = "#6b7089"


def score_guess(secret: str, guess: str) -> list[str]:
    """Pure rule: score each letter as ``"hit"`` | ``"present"`` | ``"miss"``.

    Standard Wordle duplicate handling: greens are resolved first, then a letter
    only marks ``"present"`` as many times as it remains in the secret.
    """
    secret = secret.lower()
    guess = guess.lower()
    result = ["miss"] * 5
    remaining: dict[str, int] = {}

    # First pass: exact-position hits, tally leftover secret letters.
    for i in range(5):
        if guess[i] == secret[i]:
            result[i] = "hit"
        else:
            remaining[secret[i]] = remaining.get(secret[i], 0) + 1

    # Second pass: presents, consuming leftover letters.
    for i in range(5):
        if result[i] == "hit":
            continue
        ch = guess[i]
        if remaining.get(ch, 0) > 0:
            result[i] = "present"
            remaining[ch] -= 1

    return result


def _render(console: Console, guess: str, scored: list[str]) -> None:
    """Print one scored guess as colored letters."""
    color = {"hit": GREEN, "present": YELLOW, "miss": GREY}
    cells = [
        f"[bold {color[s]}] {ch.upper()} [/bold {color[s]}]"
        for ch, s in zip(guess.lower(), scored)
    ]
    console.print("  " + "".join(cells))


def _play(console: Console) -> None:
    header(console, GAME)
    secret = random.choice(WORDS)
    max_tries = 6
    console.print(
        f"  [dim]Guess the 5-letter word. {max_tries} tries. "
        f"(q to quit)[/dim]\n"
    )

    tries = 0
    while tries < max_tries:
        raw = ask_line(console, "guess:")
        low = raw.lower()
        if low in ("q", "quit", ""):
            console.print(f"\n  [dim]The word was {secret.upper()}. Later![/dim]")
            return
        if len(low) != 5 or not low.isalpha() or not low.isascii():
            console.print("  [yellow]exactly 5 letters (a-z), please[/yellow]")
            continue

        tries += 1
        scored = score_guess(secret, low)
        _render(console, low, scored)

        if all(s == "hit" for s in scored):
            console.print(
                f"\n  [bold {GREEN}]🟩 Solved it in {tries}/"
                f"{max_tries}![/bold {GREEN}]"
            )
            return
        console.print(f"  [dim]{max_tries - tries} left[/dim]\n")

    console.print(
        f"\n  [bold {YELLOW}]Out of tries — the word was "
        f"{secret.upper()}.[/bold {YELLOW}]"
    )


GAME = GameMeta(key="wordle", name="Pandle", emoji="🟩",
                desc="guess the 5-letter word in 6 tries", play=_play)
