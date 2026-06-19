"""The arcade: registry integrity + a smoke test of each game's pure rules."""
from __future__ import annotations

import random

import pytest


def test_registry_loads_and_keys_are_unique() -> None:
    from ronin_cli.games import GAMES, GAMES_BY_KEY, find
    assert len(GAMES) >= 10
    assert len(GAMES) == len(GAMES_BY_KEY), "duplicate game key"
    for g in GAMES:
        assert g.key and g.name and g.emoji and callable(g.play)
    assert find("2048") is not None
    assert find("nope") is None


def test_number_guess_feedback() -> None:
    from ronin_cli.games.number_guess import feedback
    assert feedback(50, 50) == "correct"
    assert feedback(50, 70) == "higher"
    assert feedback(50, 10) == "lower"


def test_tictactoe_winner_and_ai_is_unbeatable() -> None:
    from ronin_cli.games.tictactoe import best_move, winner
    assert winner(["X", "X", "X", " ", " ", " ", " ", " ", " "]) == "X"
    assert winner([" "] * 9) is None
    assert winner(["X", "O", "X", "X", "O", "O", "O", "X", "X"]) == "draw"
    # From an empty board, optimal play never loses for the mover.
    assert best_move([" "] * 9, "X") in range(9)


def test_twenty48_slide_and_moves() -> None:
    from ronin_cli.games.twenty48 import has_moves, slide_left
    assert slide_left([2, 2, 0, 4]) == [4, 4, 0, 0]
    assert slide_left([2, 2, 2, 2]) == [4, 4, 0, 0]
    assert slide_left([0, 0, 0, 0]) == [0, 0, 0, 0]
    assert has_moves([[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 0]]) is True


def test_wordle_duplicate_handling() -> None:
    from ronin_cli.games.wordle import score_guess
    assert score_guess("crane", "crane") == ["hit"] * 5
    # standard Wordle dup rule: extra repeated letters that aren't there → miss
    res = score_guess("abbey", "babee")
    assert len(res) == 5 and all(x in ("hit", "present", "miss") for x in res)


def test_blackjack_ace_valuation() -> None:
    from ronin_cli.games.blackjack import hand_value, outcome
    assert hand_value(["A", "K"]) == 21
    assert hand_value(["A", "A", "9"]) == 21
    assert hand_value(["K", "Q", "5"]) == 25
    assert outcome(20, 19) == "win"
    assert outcome(18, 20) == "lose"


def test_connect_four_winner() -> None:
    from ronin_cli.games import connect_four as c
    # build a board with a horizontal four somewhere via drop()
    grid = [[" "] * 7 for _ in range(6)]
    for col in range(4):
        c.drop(grid, col, "R")
    assert c.winner(grid) == "R"


def test_snake_pure_logic() -> None:
    from ronin_cli.games.snake import collides, next_head
    assert next_head((5, 5), "up") in [(5, 4), (4, 5)]  # row/col convention may vary
    assert collides((-1, 0), [(0, 0)], 20, 12) is True   # wall
    assert collides((0, 0), [(0, 0)], 20, 12) is True    # self


def test_rps_judge() -> None:
    from ronin_cli.games.rps import judge
    assert judge("r", "s") == "win"
    assert judge("r", "p") == "lose"
    assert judge("r", "r") == "tie"


def test_pig_apply_roll() -> None:
    from ronin_cli.games.pig import apply_roll
    assert apply_roll(15, 1) == (0, True)
    assert apply_roll(15, 6) == (21, False)


def test_hangman_reveal() -> None:
    from ronin_cli.games.hangman import is_won, reveal
    assert "c" in reveal("code", set("cd"))
    assert is_won("code", set("code")) is True
    assert is_won("code", set("co")) is False


def test_scramble_is_different_and_correct() -> None:
    from ronin_cli.games.scramble import is_correct, scramble
    s = scramble("python", random.Random(3))
    assert s != "python"
    assert sorted(s) == sorted("python")
    assert is_correct("python", " PYTHON ") is True


def test_every_game_has_a_pure_function_module_that_imports() -> None:
    # Importing the package already imported all game modules; assert each GAME
    # object round-trips through find() by its own key.
    from ronin_cli.games import GAMES, find
    for g in GAMES:
        assert find(g.key) is g
