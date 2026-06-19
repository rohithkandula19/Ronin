"""ronin's arcade — a small collection of free terminal games.

Run ``ronin play`` for the picker menu, or ``ronin play <key>`` to jump straight
into one. Every game is a module exposing a ``GAME`` :class:`GameMeta`; they are
gathered here into :data:`GAMES` (kept sorted by name for a stable menu).
"""
from __future__ import annotations

from ._engine import GameMeta
from . import (
    blackjack,
    connect_four,
    hangman,
    memory,
    minesweeper,
    number_guess,
    pig,
    rps,
    scramble,
    simon,
    snake,
    tictactoe,
    twenty48,
    wordle,
)

GAMES: list[GameMeta] = sorted(
    (
        blackjack.GAME,
        connect_four.GAME,
        hangman.GAME,
        memory.GAME,
        minesweeper.GAME,
        number_guess.GAME,
        pig.GAME,
        rps.GAME,
        scramble.GAME,
        simon.GAME,
        snake.GAME,
        tictactoe.GAME,
        twenty48.GAME,
        wordle.GAME,
    ),
    key=lambda g: g.name.lower(),
)

GAMES_BY_KEY: dict[str, GameMeta] = {g.key: g for g in GAMES}


def find(key: str) -> GameMeta | None:
    """Look up a game by its stable ``key`` (case-insensitive)."""
    return GAMES_BY_KEY.get((key or "").strip().lower())


__all__ = ["GameMeta", "GAMES", "GAMES_BY_KEY", "find"]
