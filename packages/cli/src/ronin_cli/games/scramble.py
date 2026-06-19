"""Word Scramble — unscramble the jumbled word.

A random word is shuffled and shown to the player, who guesses the original.
Score and streak are tracked across rounds. The rules live in pure functions
(``scramble``, ``is_correct``) so they can be unit-tested without a terminal;
``_play`` wires them to a Rich console.
"""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

WORDS = [
    "python", "ronin", "kernel", "buffer", "cursor", "lambda",
    "syntax", "binary", "vector", "thread", "socket", "module",
    "branch", "commit", "docker", "tensor", "cipher", "schema",
    "pointer", "compiler", "variable", "function", "iterator",
    "closure", "boolean", "integer", "decorator", "generator",
    "package", "library", "runtime", "process", "garbage",
    "network", "request", "response", "session", "gateway",
    "monster", "wizard", "dragon", "castle", "puzzle", "rhythm",
]


def scramble(word: str, rng: random.Random) -> str:
    """Return ``word`` with its letters shuffled using ``rng``.

    For words longer than one character the result is guaranteed to differ
    from the original. Words with no other distinct arrangement (e.g. all
    identical letters) fall back to returning the original unchanged.
    """
    if len(word) <= 1:
        return word
    chars = list(word)
    # If every char is identical there's no distinct scramble; bail out.
    if len(set(chars)) == 1:
        return word
    for _ in range(100):
        rng.shuffle(chars)
        candidate = "".join(chars)
        if candidate != word:
            return candidate
    # Extremely unlikely fallback: force a difference via a single swap.
    chars = list(word)
    for i in range(1, len(chars)):
        if chars[i] != chars[0]:
            chars[0], chars[i] = chars[i], chars[0]
            break
    return "".join(chars)


def is_correct(word: str, guess: str) -> bool:
    """True when ``guess`` matches ``word`` (case-insensitive, trimmed)."""
    return guess.strip().lower() == word.strip().lower()


def _play(console: Console) -> None:
    header(console, GAME)
    rng = random.Random()
    score = 0
    streak = 0
    console.print(
        "  [#6b7089]Unscramble the word. "
        "(skip to reveal, q to quit)[/#6b7089]\n"
    )

    while True:
        word = rng.choice(WORDS)
        jumble = scramble(word, rng)
        console.print(
            f"  [bold #2dd4bf]{' '.join(jumble.upper())}[/bold #2dd4bf]"
            f"   [#6b7089]({len(word)} letters)[/#6b7089]"
        )
        console.print(
            f"  [#7dd3fc]score: {score}[/#7dd3fc]   "
            f"[#e0af68]streak: {streak}[/#e0af68]\n"
        )

        raw = ask_line(console, "unscramble:")
        low = raw.lower()
        if low in ("q", "quit", ""):
            console.print(
                f"\n  [#6b7089]The word was '{word}'. "
                f"Final score: {score}. Later![/#6b7089]"
            )
            return
        if low == "skip":
            console.print(f"  [#e0af68]↪ It was '{word}'.[/#e0af68]\n")
            streak = 0
            continue

        if is_correct(word, raw):
            score += 1
            streak += 1
            console.print(
                f"  [bold #9ece6a]✓ Yes! '{word}' "
                f"(streak {streak})[/bold #9ece6a]\n"
            )
        else:
            streak = 0
            console.print(
                f"  [#f7768e]✗ Nope — it was '{word}'.[/#f7768e]\n"
            )


GAME = GameMeta(
    key="scramble",
    name="Word Scramble",
    emoji="🔤",
    desc="unscramble the jumbled word",
    play=_play,
)
