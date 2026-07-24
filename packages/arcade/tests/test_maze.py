"""Maze — unit tests for the pure rules (generate_maze / neighbors / solve)."""
from __future__ import annotations

import random

import pytest

from ronin_arcade.games import maze as M


def test_generate_maze_is_deterministic_for_a_seed() -> None:
    a = M.generate_maze(8, 8, random.Random(1))
    b = M.generate_maze(8, 8, random.Random(1))
    assert a == b, "same seed must reproduce the exact same maze"
    # A different seed should (overwhelmingly likely) carve a different maze.
    assert M.generate_maze(8, 8, random.Random(2)) != a


def test_generate_maze_is_a_perfect_maze() -> None:
    w, h = 10, 7
    maze = M.generate_maze(w, h, random.Random(5))
    # Every grid cell is present as a key.
    assert set(maze.keys()) == {(x, y) for x in range(w) for y in range(h)}
    # Passages are symmetric (a opens to b  <=>  b opens to a).
    for cell, conns in maze.items():
        for nb in conns:
            assert cell in maze[nb]
    # Spanning tree: exactly N-1 undirected edges (connected, no loops).
    edges = sum(len(v) for v in maze.values()) // 2
    assert edges == w * h - 1


def test_neighbors_respects_bounds() -> None:
    assert sorted(M.neighbors((0, 0), 3, 3)) == [(0, 1), (1, 0)]   # corner → 2
    assert len(M.neighbors((1, 1), 3, 3)) == 4                     # interior → 4
    assert M.neighbors((0, 0), 1, 1) == []                         # lone cell → 0


@pytest.mark.parametrize(
    "w,h,seed",
    [(2, 2, 0), (8, 8, 1), (8, 8, 7), (12, 9, 3), (20, 12, 42), (5, 15, 99)],
)
def test_solve_always_finds_a_path(w: int, h: int, seed: int) -> None:
    maze = M.generate_maze(w, h, random.Random(seed))
    start, end = (0, 0), (w - 1, h - 1)
    path = M.solve(maze, start, end, w, h)
    assert path, "a perfect maze is always solvable — solve must return a path"
    assert path[0] == start and path[-1] == end
    # Each step is to an orthogonally-adjacent cell through an OPEN passage.
    for a, b in zip(path, path[1:]):
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1
        assert b in maze[a]


def test_solve_same_start_and_end() -> None:
    maze = M.generate_maze(4, 4, random.Random(0))
    assert M.solve(maze, (0, 0), (0, 0), 4, 4) == [(0, 0)]


def test_game_meta() -> None:
    assert M.GAME.key == "maze"
    assert M.GAME.name == "Maze"
    assert M.GAME.emoji == "\U0001f300"
    assert callable(M.GAME.play)
