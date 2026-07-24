"""Lights Out — unit tests for the pure rules (toggle / is_solved / make_puzzle)."""
from __future__ import annotations

import random

import pytest

from ronin_arcade.games import lights as L


def _off(n: int = 5) -> list[list[int]]:
    return [[0] * n for _ in range(n)]


def test_toggle_center_flips_five_cells() -> None:
    t = L.toggle(_off(), 2, 2)
    assert sum(sum(row) for row in t) == 5
    # exactly the cell and its 4 orthogonal neighbors are lit
    assert {(r, c) for r in range(5) for c in range(5) if t[r][c]} == {
        (2, 2), (1, 2), (3, 2), (2, 1), (2, 3)
    }


def test_toggle_corner_flips_three_cells() -> None:
    t = L.toggle(_off(), 0, 0)
    assert sum(sum(row) for row in t) == 3
    assert {(r, c) for r in range(5) for c in range(5) if t[r][c]} == {
        (0, 0), (1, 0), (0, 1)
    }


def test_toggle_edge_flips_four_cells() -> None:
    # an edge (non-corner) cell: itself + 3 in-bounds neighbors
    t = L.toggle(_off(), 0, 2)
    assert sum(sum(row) for row in t) == 4


def test_toggle_does_not_mutate_input() -> None:
    board = _off()
    L.toggle(board, 2, 2)
    assert board == _off(), "toggle must return a NEW grid, not mutate in place"


def test_toggle_is_self_inverse() -> None:
    once = L.toggle(_off(), 1, 3)
    twice = L.toggle(once, 1, 3)
    assert twice == _off()


def test_is_solved() -> None:
    assert L.is_solved(_off()) is True
    lit = _off()
    lit[4][1] = 1
    assert L.is_solved(lit) is False


def test_make_puzzle_shape_and_values() -> None:
    grid = L.make_puzzle(random.Random(0))
    assert len(grid) == 5 and all(len(row) == 5 for row in grid)
    assert all(cell in (0, 1) for row in grid for cell in row)


def test_make_puzzle_custom_dimensions() -> None:
    grid = L.make_puzzle(random.Random(1), n=4, scrambles=3)
    assert len(grid) == 4 and all(len(row) == 4 for row in grid)
    assert all(cell in (0, 1) for row in grid for cell in row)


@pytest.mark.parametrize("seed", [0, 1, 2, 7, 42, 1234])
def test_make_puzzle_is_always_solvable(seed: int) -> None:
    # Replay the exact press sequence make_puzzle used (same rng draw order):
    # because toggling is self-inverse and commutes, applying those same presses
    # returns the board to all-off — a concrete winning solution always exists.
    grid = L.make_puzzle(random.Random(seed), n=5, scrambles=8)
    replay = random.Random(seed)
    presses = [(replay.randrange(5), replay.randrange(5)) for _ in range(8)]
    for r, c in presses:
        grid = L.toggle(grid, r, c)
    assert L.is_solved(grid)


def test_game_meta() -> None:
    assert L.GAME.key == "lights"
    assert L.GAME.name == "Lights Out"
    assert L.GAME.emoji == "\U0001f4a1"
    assert callable(L.GAME.play)
