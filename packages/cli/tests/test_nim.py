"""Nim (misère) — unit tests for the pure rules (nim_sum / best_move)."""
from __future__ import annotations

import pytest

from ronin_cli.games import nim as N


def _apply(piles: list[int], move: tuple[int, int]) -> list[int]:
    """Apply ``(pile_index, take_count)`` to a copy of ``piles``."""
    i, take = move
    out = piles[:]
    out[i] -= take
    return out


# --- nim_sum: it is exactly the XOR of all pile sizes ----------------------
def test_nim_sum_is_xor() -> None:
    assert N.nim_sum([3, 4, 5]) == 3 ^ 4 ^ 5 == 2
    assert N.nim_sum([1, 3, 5, 7]) == 0          # classic balanced position
    assert N.nim_sum([]) == 0
    assert N.nim_sum([6]) == 6
    assert N.nim_sum([2, 2]) == 0


# --- best_move: returns a legal, optimal move ------------------------------
def test_best_move_from_345_zeroes_the_nim_sum() -> None:
    move = N.best_move([3, 4, 5])
    after = _apply([3, 4, 5], move)
    assert N.nim_sum(after) == 0                 # required: applied move -> nim_sum 0


def test_best_move_is_always_legal() -> None:
    for piles in ([3, 4, 5], [1, 3, 5, 7], [2, 4, 6], [5], [1, 1], [2, 1, 1]):
        i, take = N.best_move(piles)
        assert 0 <= i < len(piles)
        assert 1 <= take <= piles[i]


def test_best_move_normal_play_drives_nim_sum_to_zero() -> None:
    # With two or more big piles and non-zero nim-sum, the move zeroes it.
    for piles in ([3, 4, 5], [2, 3, 4], [4, 5, 7]):
        assert N.nim_sum(piles) != 0
        assert N.nim_sum(_apply(piles, N.best_move(piles))) == 0


def test_best_move_endgame_leaves_an_odd_number_of_ones() -> None:
    # All piles are single stones with an EVEN count -> mover wins by leaving odd.
    after = _apply([1, 1, 1, 1], N.best_move([1, 1, 1, 1]))
    assert sum(after) % 2 == 1                    # 3 ones left — odd


def test_best_move_handoff_override_beats_naive_nim() -> None:
    # [2, 1, 1]: naively zeroing the nim-sum gives [1, 1] (even -> losing).
    # The perfect misère move takes 1 from the big pile -> [1, 1, 1] (odd).
    move = N.best_move([2, 1, 1])
    after = _apply([2, 1, 1], move)
    assert sorted(after) == [1, 1, 1]
    assert sum(1 for n in after if n == 1) % 2 == 1


def test_best_move_requires_a_stone() -> None:
    with pytest.raises(ValueError):
        N.best_move([0, 0, 0])


def test_mover_wins_classification() -> None:
    assert N.mover_wins([3, 4, 5]) is True        # nim-sum 2 -> N-position
    assert N.mover_wins([1, 3, 5, 7]) is False     # nim-sum 0 -> P-position
    assert N.mover_wins([1, 1]) is True            # even ones -> win
    assert N.mover_wins([1, 1, 1]) is False         # odd ones  -> loss


def test_game_meta() -> None:
    assert N.GAME.key == "nim"
    assert N.GAME.name == "Nim"
    assert N.GAME.emoji == "🪙"
    assert callable(N.GAME.play)
