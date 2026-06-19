"""Guess the Output — unit tests for the pure bank + grader (no terminal)."""
from __future__ import annotations

from ronin_cli.games import guessout as G


def test_bank_is_substantial_and_well_formed() -> None:
    assert len(G.PUZZLES) >= 10
    for p in G.PUZZLES:
        assert "code" in p and "output" in p and "why" in p
        code = p["code"]
        assert isinstance(code, (str, list)) and code            # str or list, non-empty
        assert isinstance(p["output"], str) and p["output"].strip()
        assert isinstance(p["why"], str) and p["why"].strip()


def test_check_accepts_exact_output_and_rejects_wrong() -> None:
    for p in G.PUZZLES:
        assert G.check(p, p["output"]) is True
        assert G.check(p, "WRONG") is False


def test_check_forgives_surrounding_whitespace() -> None:
    p = G.PUZZLES[0]
    assert G.check(p, "  " + p["output"] + "  ") is True
    assert G.check(p, "\n\t" + p["output"] + "  \n") is True


def test_check_is_value_and_case_sensitive() -> None:
    p = G.PUZZLES[0]
    assert G.check(p, p["output"] + "x") is False          # value-sensitive
    assert G.check(p, p["output"].upper()) == (p["output"] == p["output"].upper())


def test_check_handles_none_and_empty_safely() -> None:
    p = G.PUZZLES[0]
    assert G.check(p, None) is False
    assert G.check(p, "") is False


def test_game_meta() -> None:
    assert G.GAME.key == "guessout"
    assert G.GAME.name == "Guess the Output"
    assert G.GAME.emoji == "\U0001f5a8️"
    assert G.GAME.desc == "predict what the code prints"
    assert callable(G.GAME.play)
