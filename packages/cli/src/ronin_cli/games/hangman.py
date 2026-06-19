"""Hangman — guess the word before the gallows is fully drawn.

A real game of hangman: words are drawn from four categories (animals, coding,
food, countries) and the category is shown as a hint. Each wrong guess advances
a 7-stage ASCII gallows; a row of already-guessed letters and a row of filling
blanks keep the player oriented.

The rules live in pure functions (``reveal``, ``is_won``) so they can be
unit-tested without a terminal; ``_play`` wires them to a Rich console.
"""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# --- palette --------------------------------------------------------------
TEAL = "#2dd4bf"
GOOD = "#9ece6a"
WARN = "#e0af68"
ERROR = "#f7768e"
SKY = "#7dd3fc"
MUTE = "#6b7089"

# --- words by category ----------------------------------------------------
# 60+ words total. Multi-word entries use spaces; only letters are guessed.
CATEGORIES: dict[str, list[str]] = {
    "animals": [
        "otter", "lemur", "gecko", "raven", "tiger", "koala", "moose",
        "heron", "viper", "lynx", "panda", "falcon", "iguana", "walrus",
        "badger", "ocelot", "mantis", "narwhal",
    ],
    "coding": [
        "python", "ronin", "kernel", "buffer", "cursor", "lambda", "syntax",
        "binary", "vector", "thread", "socket", "module", "branch", "commit",
        "docker", "tensor", "pixel", "cipher", "schema", "compiler",
        "variable", "function", "recursion",
    ],
    "food": [
        "ramen", "mango", "waffle", "pickle", "noodle", "cheese", "pretzel",
        "avocado", "biscuit", "pancake", "burrito", "dumpling", "espresso",
        "tiramisu", "macaron", "gnocchi", "falafel", "wasabi",
    ],
    "countries": [
        "japan", "brazil", "kenya", "norway", "canada", "mexico", "iceland",
        "morocco", "vietnam", "belgium", "ecuador", "denmark", "portugal",
        "thailand", "tanzania", "australia", "argentina", "switzerland",
    ],
}

MAX_WRONG = 6

# Progressive ASCII gallows; index == number of wrong guesses (0..MAX_WRONG).
# 7 distinct stages: empty gallows → head → torso → one arm → both arms →
# one leg → both legs (full figure).
GALLOWS = [
    r"""
   +----+
   |    |
        |
        |
        |
        |
  ==========""",
    r"""
   +----+
   |    |
   O    |
        |
        |
        |
  ==========""",
    r"""
   +----+
   |    |
   O    |
   |    |
   |    |
        |
  ==========""",
    r"""
   +----+
   |    |
   O    |
  /|    |
   |    |
        |
  ==========""",
    r"""
   +----+
   |    |
   O    |
  /|\   |
   |    |
        |
  ==========""",
    r"""
   +----+
   |    |
   O    |
  /|\   |
   |    |
  /     |
  ==========""",
    r"""
   +----+
   |    |
   O    |
  /|\   |
   |    |
  / \   |
  ==========""",
]


def reveal(word: str, guessed: set[str]) -> str:
    """Render the word with guessed letters shown, others as ``_``.

    Spaces in multi-word answers are kept as spaces.
    e.g. ``reveal("code", {"c", "d"}) == "c _ d _"``.
    """
    return " ".join(
        " " if ch == " " else (ch if ch in guessed else "_") for ch in word
    )


def is_won(word: str, guessed: set[str]) -> bool:
    """True when every (non-space) letter of ``word`` has been guessed."""
    return all(ch == " " or ch in guessed for ch in word)


# --- rendering helpers ----------------------------------------------------
ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def _alphabet_row(word: str, guessed: set[str]) -> str:
    """A row of a-z, dimming used letters and coloring hits/misses."""
    cells = []
    for ch in ALPHABET:
        if ch not in guessed:
            cells.append(f"[{MUTE}]{ch}[/{MUTE}]")
        elif ch in word:
            cells.append(f"[{GOOD}]{ch}[/{GOOD}]")
        else:
            cells.append(f"[{ERROR}]{ch}[/{ERROR}]")
    return " ".join(cells)


def _draw(console: Console, word: str, guessed: set[str], category: str,
          wrong: int) -> None:
    remaining = MAX_WRONG - wrong
    misses = sorted(g for g in guessed if g not in word)
    miss_str = ("  ".join(f"[{ERROR}]{m}[/{ERROR}]" for m in misses)
                if misses else f"[{MUTE}]none[/{MUTE}]")

    console.print(f"[{MUTE}]{GALLOWS[wrong]}[/{MUTE}]")
    console.print()
    console.print(f"  [{SKY}]category[/{SKY}]  [bold {TEAL}]{category}[/bold {TEAL}]")
    console.print(f"  [bold {GOOD}]{reveal(word, guessed)}[/bold {GOOD}]")
    console.print()
    console.print(f"  [{MUTE}]{_alphabet_row(word, guessed)}[/{MUTE}]")
    console.print(
        f"  [{SKY}]misses left: {remaining}[/{SKY}]"
        f"    [{WARN}]wrong:[/{WARN}] {miss_str}"
    )
    console.print()


def _play(console: Console) -> None:
    header(console, GAME)
    category = random.choice(list(CATEGORIES))
    word = random.choice(CATEGORIES[category])
    guessed: set[str] = set()
    wrong = 0

    console.print(
        f"  [{MUTE}]Guess the word one letter at a time. "
        f"Category is your hint. (q to quit)[/{MUTE}]"
    )

    while True:
        _draw(console, word, guessed, category, wrong)

        raw = ask_line(console, "letter:").lower()
        if raw in ("q", "quit", ""):
            console.print(f"\n  [{MUTE}]The word was '{word}'. Later![/{MUTE}]\n")
            return
        if len(raw) != 1 or not ("a" <= raw <= "z"):
            console.print(f"  [{ERROR}]single a-z letter only[/{ERROR}]\n")
            continue
        if raw in guessed:
            console.print(f"  [{WARN}]you already tried '{raw}' — pick another[/{WARN}]\n")
            continue

        guessed.add(raw)
        if raw in word:
            if is_won(word, guessed):
                _draw(console, word, guessed, category, wrong)
                console.print(
                    f"  [bold {GOOD}]🎉 You got it: {word.upper()}![/bold {GOOD}]\n"
                )
                return
            console.print(f"  [{GOOD}]✓ '{raw}' is in the word[/{GOOD}]\n")
        else:
            wrong += 1
            if wrong >= MAX_WRONG:
                console.print(f"[{MUTE}]{GALLOWS[MAX_WRONG]}[/{MUTE}]")
                console.print(
                    f"\n  [bold {ERROR}]💀 Game over. The word was "
                    f"'{word.upper()}'.[/bold {ERROR}]\n"
                )
                return
            console.print(f"  [{ERROR}]✗ no '{raw}'[/{ERROR}]\n")


GAME = GameMeta(
    key="hangman",
    name="Hangman",
    emoji="🎯",
    desc="guess the word before the gallows is drawn",
    play=_play,
)
