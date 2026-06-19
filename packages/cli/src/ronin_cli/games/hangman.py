"""Hangman — guess the word before the panda is fully drawn.

The rules live in pure functions (``reveal``, ``is_won``, ``GALLOWS``) so they
can be unit-tested without a terminal; ``_play`` wires them to a Rich console.
"""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

WORDS = [
    "python", "ronin", "panda", "kernel", "buffer", "cursor", "lambda",
    "syntax", "binary", "vector", "thread", "socket", "module", "branch",
    "commit", "docker", "tensor", "pixel", "cipher", "schema",
    "otter", "lemur", "gecko", "raven", "tiger", "koala", "moose",
    "heron", "viper", "lynx",
]

MAX_WRONG = 6

# Progressive ASCII gallows; index == number of wrong guesses (0..MAX_WRONG).
GALLOWS = [
    r"""
  +---+
  |   |
      |
      |
      |
      |
=========""",
    r"""
  +---+
  |   |
  O   |
      |
      |
      |
=========""",
    r"""
  +---+
  |   |
  O   |
  |   |
      |
      |
=========""",
    r"""
  +---+
  |   |
  O   |
 /|   |
      |
      |
=========""",
    r"""
  +---+
  |   |
  O   |
 /|\  |
      |
      |
=========""",
    r"""
  +---+
  |   |
  O   |
 /|\  |
 /    |
      |
=========""",
    r"""
  +---+
  |   |
  O   |
 /|\  |
 / \  |
      |
=========""",
]


def reveal(word: str, guessed: set[str]) -> str:
    """Render the word with guessed letters shown, others as ``_``.

    e.g. ``reveal("code", {"c", "d"}) == "c _ d _"``.
    """
    return " ".join(ch if ch in guessed else "_" for ch in word)


def is_won(word: str, guessed: set[str]) -> bool:
    """True when every letter of ``word`` has been guessed."""
    return all(ch in guessed for ch in word)


def _play(console: Console) -> None:
    header(console, GAME)
    word = random.choice(WORDS)
    guessed: set[str] = set()
    wrong = 0
    console.print("  [#6b7089]Guess the word, one letter at a time. (q to quit)[/#6b7089]\n")

    while True:
        console.print(f"[#6b7089]{GALLOWS[wrong]}[/#6b7089]")
        console.print(f"\n  [bold #2dd4bf]{reveal(word, guessed)}[/bold #2dd4bf]")
        remaining = MAX_WRONG - wrong
        missed = sorted(g for g in guessed if g not in word)
        miss_str = (" ".join(missed)) if missed else "none"
        console.print(
            f"  [#7dd3fc]misses left: {remaining}[/#7dd3fc]   "
            f"[#e0af68]wrong: {miss_str}[/#e0af68]\n"
        )

        raw = ask_line(console, "letter:").lower()
        if raw in ("q", "quit", ""):
            console.print(f"\n  [#6b7089]The word was '{word}'. Later![/#6b7089]")
            return
        if len(raw) != 1 or not ("a" <= raw <= "z"):
            console.print("  [#f7768e]single a-z letter only[/#f7768e]\n")
            continue
        if raw in guessed:
            console.print(f"  [#e0af68]already tried '{raw}'[/#e0af68]\n")
            continue

        guessed.add(raw)
        if raw in word:
            if is_won(word, guessed):
                console.print(f"\n  [bold #9ece6a]🎯 You got it: {word}![/bold #9ece6a]")
                return
            console.print(f"  [#9ece6a]✓ '{raw}' is in the word[/#9ece6a]\n")
        else:
            wrong += 1
            if wrong >= MAX_WRONG:
                console.print(f"[#6b7089]{GALLOWS[wrong]}[/#6b7089]")
                console.print(f"\n  [bold #f7768e]💀 The panda is drawn. The word was '{word}'.[/bold #f7768e]")
                return
            console.print(f"  [#f7768e]✗ no '{raw}'[/#f7768e]\n")


GAME = GameMeta(
    key="hangman",
    name="Hangman",
    emoji="🎯",
    desc="guess the word before the panda is drawn",
    play=_play,
)
