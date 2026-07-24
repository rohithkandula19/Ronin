"""Mastermind — crack ronin's secret colour code in 10 guesses.

A real Mastermind: a hidden code of four pegs drawn from six colours (repeats
allowed). Each guess is scored with *black* pegs (right colour in the right
spot) and *white* pegs (right colour, wrong spot) using the standard rules —
every peg in the code is matched at most once, blacks resolved before whites.
The rule logic lives in pure, I/O-free functions (``score`` / ``make_secret``)
so it can be unit-tested without a terminal; Rich draws the coloured board.
"""
from __future__ import annotations

import random
from collections import Counter

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #
# The six code colours, keyed by their single guess-letter.
COLORS: str = "RGBYOP"

HEX: dict[str, str] = {
    "R": "#f7768e",
    "G": "#9ece6a",
    "B": "#7dd3fc",
    "Y": "#e0af68",
    "O": "#ff9e64",
    "P": "#bb9af7",
}

NAMES: dict[str, str] = {
    "R": "red",
    "G": "green",
    "B": "blue",
    "Y": "yellow",
    "O": "orange",
    "P": "purple",
}

TEAL = "#2dd4bf"   # accent
MUTE = "#6b7089"   # muted text
DARK = "#1a1b26"   # foreground on coloured pegs

CODE_LENGTH = 4
MAX_GUESSES = 10


# --------------------------------------------------------------------------- #
# Pure rules (I/O-free — unit-tested)
# --------------------------------------------------------------------------- #
def make_secret(rng: random.Random, length: int = 4, colors: str = COLORS) -> list[str]:
    """Pick a random secret code of ``length`` pegs from ``colors`` (repeats ok)."""
    return [rng.choice(colors) for _ in range(length)]


def score(secret: list[str], guess: list[str]) -> tuple[int, int]:
    """Standard Mastermind scoring → ``(black, white)``.

    Blacks are exact colour+position matches, counted first. Whites are right
    colour / wrong position, drawn only from the pegs left over after blacks —
    so each peg in the code (and each in the guess) is counted at most once.
    """
    black = sum(s == g for s, g in zip(secret, guess))

    # Tally the unmatched pegs on each side, then the whites are the overlap of
    # those two multisets (capped per colour by whichever side has fewer left).
    secret_left = Counter(s for s, g in zip(secret, guess) if s != g)
    guess_left = Counter(g for s, g in zip(secret, guess) if s != g)
    white = sum(min(secret_left[c], guess_left[c]) for c in guess_left)

    return black, white


def parse_guess(raw: str, length: int = CODE_LENGTH, colors: str = COLORS) -> list[str] | None:
    """Validate/normalise a typed guess like ``"rgby"`` → ``["R","G","B","Y"]``.

    Returns ``None`` for anything of the wrong length or with unknown colours.
    """
    pegs = raw.strip().upper()
    if len(pegs) != length:
        return None
    if any(c not in colors for c in pegs):
        return None
    return list(pegs)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _peg(color: str) -> str:
    """A single coloured code peg: its letter on its colour."""
    return f"[bold {DARK} on {HEX[color]}] {color} [/]"


def _hidden_peg() -> str:
    return f"[{MUTE}] ? [/]"


def _feedback(black: int, white: int) -> str:
    """Render the feedback as black ● and white ○ pegs, padded with dots."""
    blanks = CODE_LENGTH - black - white
    parts = (
        [f"[bold {DARK}]●[/]"] * black            # ● solid black peg
        + [f"[bold white]○[/]"] * white           # ○ hollow white peg
        + [f"[{MUTE}]·[/]"] * blanks              # · empty slot
    )
    return " ".join(parts)


def _legend(console: Console) -> None:
    """Print the six colours and their letters."""
    swatches = "   ".join(
        f"[bold {DARK} on {HEX[c]}] {c} [/] [{MUTE}]{NAMES[c]}[/]" for c in COLORS
    )
    console.print(f"  {swatches}")


def _render_board(
    console: Console,
    guesses: list[tuple[list[str], int, int]],
) -> None:
    """Render the history board: every guess row with its black/white feedback."""
    console.print()
    for i in range(MAX_GUESSES):
        n = f"[{MUTE}]{i + 1:>2}[/]"
        if i < len(guesses):
            pegs, black, white = guesses[i]
            row = " ".join(_peg(c) for c in pegs)
            fb = _feedback(black, white)
            tally = f"[{MUTE}]({black}● {white}○)[/]"
            console.print(f"  {n}  {row}   {fb}   {tally}")
        else:
            row = " ".join(_hidden_peg() for _ in range(CODE_LENGTH))
            console.print(f"  {n}  {row}")
    console.print()


def _reveal(console: Console, secret: list[str]) -> str:
    return " ".join(_peg(c) for c in secret)


# --------------------------------------------------------------------------- #
# Play loop
# --------------------------------------------------------------------------- #
def _play(console: Console) -> None:
    header(console, GAME)
    rng = random.Random()
    secret = make_secret(rng, CODE_LENGTH, COLORS)

    console.print(
        f"  [{MUTE}]Crack the secret code of [bold {TEAL}]{CODE_LENGTH}[/bold {TEAL}] "
        f"colours in [bold {TEAL}]{MAX_GUESSES}[/bold {TEAL}] guesses "
        f"(repeats allowed).[/]"
    )
    console.print(
        f"  [{MUTE}]Type the colour letters, e.g. [bold {TEAL}]rgby[/bold {TEAL}]. "
        f"[bold {DARK}]●[/] = right colour & spot, "
        f"[bold white]○[/] = right colour wrong spot. "
        f"[bold {TEAL}]q[/bold {TEAL}] to quit.[/]"
    )
    console.print()
    _legend(console)

    guesses: list[tuple[list[str], int, int]] = []
    _render_board(console, guesses)

    while len(guesses) < MAX_GUESSES:
        left = MAX_GUESSES - len(guesses)
        raw = ask_line(console, f"guess ({left} left):")
        low = raw.strip().lower()

        if low in ("q", "quit") or raw == "":
            console.print(
                f"\n  [{MUTE}]The code was[/]  {_reveal(console, secret)}"
                f"   [{MUTE}]Later![/]\n"
            )
            return

        guess = parse_guess(raw, CODE_LENGTH, COLORS)
        if guess is None:
            console.print(
                f"  [{HEX['Y']}]Enter exactly {CODE_LENGTH} colour letters "
                f"from [bold]{COLORS}[/bold].[/]"
            )
            continue

        black, white = score(secret, guess)
        guesses.append((guess, black, white))
        _render_board(console, guesses)

        if black == CODE_LENGTH:
            console.print(
                f"  [bold {HEX['G']}]Cracked it in {len(guesses)}/"
                f"{MAX_GUESSES}![/bold {HEX['G']}]  {_reveal(console, secret)}\n"
            )
            return

    console.print(
        f"  [bold {HEX['Y']}]Out of guesses[/bold {HEX['Y']}] — the code was  "
        f"{_reveal(console, secret)}\n"
    )


GAME = GameMeta(key="mastermind", name="Mastermind", emoji="\U0001f3a8",
                desc="crack the secret colour code", play=_play)
