"""ronin's arcade — a small collection of free terminal games.

Run ``ronin play`` for the picker menu, or ``ronin play <key>`` to jump straight
into one. Every game is a module exposing a ``GAME`` :class:`GameMeta`; they are
gathered here into :data:`GAMES` (kept sorted by name for a stable menu).
"""
from __future__ import annotations

from ._engine import GameMeta
from . import (
    battleship,
    bigo,
    blackjack,
    bughunt,
    connect_four,
    hangman,
    mastermind,
    memory,
    minesweeper,
    number_guess,
    pig,
    regexgolf,
    reversi,
    rps,
    scramble,
    simon,
    snake,
    sudoku,
    tetris,
    tictactoe,
    twenty48,
    typing,
    wordle,
)

GAMES: list[GameMeta] = sorted(
    (
        battleship.GAME,
        bigo.GAME,
        blackjack.GAME,
        bughunt.GAME,
        connect_four.GAME,
        hangman.GAME,
        mastermind.GAME,
        memory.GAME,
        minesweeper.GAME,
        number_guess.GAME,
        pig.GAME,
        regexgolf.GAME,
        reversi.GAME,
        rps.GAME,
        scramble.GAME,
        simon.GAME,
        snake.GAME,
        sudoku.GAME,
        tetris.GAME,
        tictactoe.GAME,
        twenty48.GAME,
        typing.GAME,
        wordle.GAME,
    ),
    key=lambda g: g.name.lower(),
)

GAMES_BY_KEY: dict[str, GameMeta] = {g.key: g for g in GAMES}


def find(key: str) -> GameMeta | None:
    """Look up a game by its stable ``key`` (case-insensitive)."""
    return GAMES_BY_KEY.get((key or "").strip().lower())


__all__ = ["GameMeta", "GAMES", "GAMES_BY_KEY", "find"]
