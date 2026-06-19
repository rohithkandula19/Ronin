"""2048 — slide tiles on a 4x4 grid and try to reach 2048.

Rules live in pure, I/O-free functions (``slide_left``, ``move``, ``has_moves``)
so they can be unit-tested without a terminal. ``_play`` drives the interactive
loop via the shared ``_engine`` helpers using typed WASD input.
"""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# Palette
ACCENT = "#2dd4bf"
INFO = "#7dd3fc"
GOOD = "#9ece6a"
WARN = "#e0af68"
ERROR = "#f7768e"
MUTE = "#6b7089"

SIZE = 4
WIN_TILE = 2048


# ---------------------------------------------------------------------------
# Pure rules (no I/O)
# ---------------------------------------------------------------------------
def slide_left(row: list[int]) -> list[int]:
    """Compress and merge one row leftward, e.g. [2,2,0,4] -> [4,4,0,0]."""
    tiles = [v for v in row if v]
    out: list[int] = []
    i = 0
    while i < len(tiles):
        if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
            out.append(tiles[i] * 2)
            i += 2
        else:
            out.append(tiles[i])
            i += 1
    out.extend([0] * (len(row) - len(out)))
    return out


def _slide_left_gain(row: list[int]) -> tuple[list[int], int]:
    """Like ``slide_left`` but also reports points gained from merges."""
    tiles = [v for v in row if v]
    out: list[int] = []
    gained = 0
    i = 0
    while i < len(tiles):
        if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
            merged = tiles[i] * 2
            out.append(merged)
            gained += merged
            i += 2
        else:
            out.append(tiles[i])
            i += 1
    out.extend([0] * (len(row) - len(out)))
    return out, gained


def _transpose(grid: list[list[int]]) -> list[list[int]]:
    return [list(col) for col in zip(*grid)]


def move(grid: list[list[int]], direction: str) -> tuple[list[list[int]], int]:
    """Apply a move. ``direction`` in {up, down, left, right}.

    Returns ``(new_grid, gained)``. Implemented by rotating the grid so every
    direction reuses :func:`slide_left`.
    """
    gained = 0

    if direction == "left":
        rows = [list(r) for r in grid]
    elif direction == "right":
        rows = [list(reversed(r)) for r in grid]
    elif direction == "up":
        rows = _transpose(grid)
    elif direction == "down":
        rows = [list(reversed(r)) for r in _transpose(grid)]
    else:
        return [list(r) for r in grid], 0

    new_rows = []
    for r in rows:
        slid, g = _slide_left_gain(r)
        new_rows.append(slid)
        gained += g

    if direction == "left":
        result = new_rows
    elif direction == "right":
        result = [list(reversed(r)) for r in new_rows]
    elif direction == "up":
        result = _transpose(new_rows)
    else:  # down
        result = _transpose([list(reversed(r)) for r in new_rows])

    return result, gained


def has_moves(grid: list[list[int]]) -> bool:
    """True if any move would change the grid (empty cell or adjacent pair)."""
    for r in range(len(grid)):
        for c in range(len(grid[r])):
            if grid[r][c] == 0:
                return True
            if c + 1 < len(grid[r]) and grid[r][c] == grid[r][c + 1]:
                return True
            if r + 1 < len(grid) and grid[r][c] == grid[r + 1][c]:
                return True
    return False


def _has_won(grid: list[list[int]]) -> bool:
    return any(v >= WIN_TILE for row in grid for v in row)


# ---------------------------------------------------------------------------
# Interactive helpers
# ---------------------------------------------------------------------------
def _empties(grid: list[list[int]]) -> list[tuple[int, int]]:
    return [(r, c) for r in range(SIZE) for c in range(SIZE) if grid[r][c] == 0]


def _spawn(grid: list[list[int]]) -> None:
    cells = _empties(grid)
    if not cells:
        return
    r, c = random.choice(cells)
    grid[r][c] = 4 if random.random() < 0.1 else 2


def _new_grid() -> list[list[int]]:
    grid = [[0] * SIZE for _ in range(SIZE)]
    _spawn(grid)
    _spawn(grid)
    return grid


def _color_for(value: int) -> str:
    if value == 0:
        return MUTE
    if value < 16:
        return INFO
    if value < 128:
        return GOOD
    if value < 1024:
        return WARN
    if value < WIN_TILE:
        return ERROR
    return ACCENT


def _render(console: Console, grid: list[list[int]], score: int) -> None:
    from rich.table import Table

    table = Table(show_header=False, show_edge=True, box=None, padding=(0, 1),
                  border_style=MUTE)
    for _ in range(SIZE):
        table.add_column(justify="right", width=6, no_wrap=True)
    for row in grid:
        cells = []
        for v in row:
            text = "·" if v == 0 else str(v)
            cells.append(f"[bold {_color_for(v)}]{text:>5}[/bold {_color_for(v)}]")
        table.add_row(*cells)
    console.print(table)
    console.print(f"  [{MUTE}]score[/{MUTE}] [bold {INFO}]{score}[/bold {INFO}]")
    console.print()


_KEYS = {"w": "up", "a": "left", "s": "down", "d": "right"}


def _play(console: Console) -> None:
    header(console, GAME)
    console.print(f"  [{MUTE}]w=up  a=left  s=down  d=right   q=quit[/{MUTE}]")
    console.print()

    grid = _new_grid()
    score = 0
    won = False

    while True:
        _render(console, grid, score)

        if not has_moves(grid):
            console.print(f"  [bold {ERROR}]game over[/bold {ERROR}] "
                          f"[{MUTE}]final score {score}[/{MUTE}]")
            console.print()
            return

        raw = ask_line(console, "move (wasd):").lower()
        if raw in ("q", "quit", ""):
            console.print(f"  [{MUTE}]bye — score {score}[/{MUTE}]")
            console.print()
            return

        key = raw[0] if raw else ""
        direction = _KEYS.get(key)
        if direction is None:
            console.print(f"  [{WARN}]use w/a/s/d (or q to quit)[/{WARN}]")
            console.print()
            continue

        new_grid, gained = move(grid, direction)
        if new_grid == grid:
            console.print(f"  [{MUTE}]no change — try another direction[/{MUTE}]")
            console.print()
            continue

        grid = new_grid
        score += gained
        _spawn(grid)

        if not won and _has_won(grid):
            won = True
            _render(console, grid, score)
            console.print(f"  [bold {ACCENT}]you reached 2048! 🔢[/bold {ACCENT}] "
                          f"[{MUTE}]keep going or q to quit[/{MUTE}]")
            console.print()


GAME = GameMeta(
    key="2048",
    name="2048",
    emoji="🔢",
    desc="slide tiles, reach 2048",
    play=_play,
)
