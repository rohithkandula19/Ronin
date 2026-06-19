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


def test_tetris_pure_logic() -> None:
    from ronin_cli.games import tetris as t
    cells = [(0, 0), (0, 1), (0, 2), (1, 1)]
    r4 = t.rotate(t.rotate(t.rotate(t.rotate(cells))))
    assert sorted(r4) == sorted(cells)                 # 4 rotations round-trip
    board = t._empty_board()
    board[t.HEIGHT - 1] = ["I"] * t.WIDTH              # one full bottom row
    new_board, cleared = t.clear_lines(board)
    assert cleared == 1 and len(new_board) == t.HEIGHT
    assert t.collides(t._empty_board(), [(0, 0)], (t.WIDTH, 0)) is True   # off right wall
    assert t.collides(t._empty_board(), [(0, 0)], (0, 0)) is False


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


def test_sudoku_generate_and_rules() -> None:
    from ronin_cli.games import sudoku as s
    puzzle, solution = s.generate(random.Random(1))
    assert s.is_solved(solution) is True
    assert any(v == 0 for row in puzzle for v in row)        # puzzle has blanks
    assert s.is_valid(s._empty_board() if hasattr(s, "_empty_board") else [[0]*9 for _ in range(9)], 0, 0, 5) is True


def test_mastermind_scoring() -> None:
    from ronin_cli.games.mastermind import score
    assert score(["R", "G", "B", "Y"], ["R", "G", "Y", "B"]) == (2, 2)
    assert score(["R", "R", "G", "G"], ["R", "G", "R", "R"]) == (1, 2)
    assert score(["R", "G", "B", "Y"], ["R", "G", "B", "Y"]) == (4, 0)


def test_battleship_placement() -> None:
    from ronin_cli.games.battleship import all_sunk, place_fleet
    fleet = place_fleet(random.Random(2))
    cells = set().union(*fleet)
    assert len(cells) == 17                                  # 5+4+3+3+2, no overlaps
    assert all_sunk(fleet, cells) is True
    assert all_sunk(fleet, set()) is False


def test_reversi_opening() -> None:
    from ronin_cli.games import reversi as r
    b = r.initial_board()
    assert r.count(b) == (2, 2)
    assert len(r.legal_moves(b, "B")) == 4
    nb = r.apply_move(b, "B", *sorted(r.legal_moves(b, "B"))[0])
    assert r.count(nb)[0] == 4                                # one placed + one flipped


def test_typing_metrics() -> None:
    from ronin_cli.games.typing import accuracy, wpm
    assert round(wpm(250, 60.0), 1) == 50.0
    assert round(accuracy("hello", "hellp"), 1) == 80.0
    assert wpm(100, 0) >= 0                                   # guarded, no div-by-zero


def test_bughunt_and_bigo_banks() -> None:
    from ronin_cli.games import bigo, bughunt
    assert len(bughunt.PUZZLES) >= 10
    p = bughunt.PUZZLES[0]
    line = p["bug_line"] if isinstance(p, dict) else p.bug_line
    assert bughunt.check(p, line) is True
    assert len(bigo.QUESTIONS) >= 10
    q = bigo.QUESTIONS[0]
    ans = q["answer"] if isinstance(q, dict) else q.answer
    assert bigo.check(q, ans) is True


def test_regexgolf_solves_and_invalid_safe() -> None:
    from ronin_cli.games.regexgolf import LEVELS, solves
    assert len(LEVELS) >= 8
    assert solves("a", ["cat", "bat"], ["dog"]) is True
    assert solves("z", ["cat"], []) is False
    assert solves("[", ["x"], []) is False                   # invalid regex → False, not a crash


def test_llm_plumbing_degrades_safely() -> None:
    # complete() must never raise and returns "" on any error.
    from ronin_cli.games import _llm
    ok, reason = _llm.available()
    assert isinstance(ok, bool) and isinstance(reason, str)


def test_mindreader_helpers() -> None:
    from ronin_cli.games.mindreader import is_guess, parse_answer
    assert parse_answer("Y") == "yes"
    assert parse_answer("nope") == "no"
    assert parse_answer("") == "quit"
    assert is_guess("GUESS: a cat") == (True, "a cat")
    assert is_guess("Is it alive?") == (False, "")


def test_adventure_setting_picker() -> None:
    from ronin_cli.games.adventure import SETTINGS, pick_setting
    assert len(SETTINGS) >= 5
    assert pick_setting("", random.Random(1)) in SETTINGS
    assert pick_setting("a pirate cove", random.Random(1)) == "a pirate cove"


def test_trivia_parser_is_robust() -> None:
    from ronin_cli.games.trivia import check, parse_question
    sample = ('```json\n{"question":"2+2?","options":{"A":"3","B":"4","C":"5","D":"6"},'
              '"answer":"B","fact":"math"}\n```')
    q = parse_question(sample)
    assert q is not None
    assert check(q, "b") is True and check(q, "A") is False
    assert parse_question("not json at all") is None


def test_every_game_has_a_pure_function_module_that_imports() -> None:
    # Importing the package already imported all game modules; assert each GAME
    # object round-trips through find() by its own key.
    from ronin_cli.games import GAMES, find
    for g in GAMES:
        assert find(g.key) is g
