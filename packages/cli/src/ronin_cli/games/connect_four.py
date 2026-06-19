"""Connect Four vs ronin — drop discs on a 7-wide x 6-tall board and try to land
four in a row before the panda does. The panda's AI takes any immediate win,
blocks your immediate win, and otherwise plays a sensible center-leaning column."""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# Palette
ACCENT = "#2dd4bf"
GOOD = "#9ece6a"
WARN = "#e0af68"
ERROR = "#f7768e"
MUTE = "#6b7089"

COLS = 7
ROWS = 6
EMPTY = " "

# Grid representation: ``grid`` is a list of ROWS rows, each a list of COLS cells.
# ``grid[0]`` is the TOP row, ``grid[ROWS - 1]`` is the BOTTOM row. A disc dropped
# into a column comes to rest in the lowest empty row.


def new_grid() -> list[list[str]]:
    """A fresh empty board (top row first)."""
    return [[EMPTY] * COLS for _ in range(ROWS)]


def drop(grid: list[list[str]], col: int, disc: str) -> int | None:
    """Drop ``disc`` into 0-indexed ``col``; return the row it filled, or None if full."""
    if col < 0 or col >= COLS:
        return None
    for row in range(ROWS - 1, -1, -1):
        if grid[row][col] == EMPTY:
            grid[row][col] = disc
            return row
    return None


def col_full(grid: list[list[str]], col: int) -> bool:
    """True if ``col`` has no empty cell left."""
    return grid[0][col] != EMPTY


def board_full(grid: list[list[str]]) -> bool:
    """True if every column is full (a draw, absent any winner)."""
    return all(col_full(grid, c) for c in range(COLS))


def winner(grid: list[list[str]]) -> str | None:
    """Return the disc char of any 4-in-a-row (horizontal/vertical/both diagonals)."""
    for r in range(ROWS):
        for c in range(COLS):
            disc = grid[r][c]
            if disc == EMPTY:
                continue
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                rr, cc = r + 3 * dr, c + 3 * dc
                if not (0 <= rr < ROWS and 0 <= cc < COLS):
                    continue
                if all(grid[r + i * dr][c + i * dc] == disc for i in range(4)):
                    return disc
    return None


def _wins_with(grid: list[list[str]], col: int, disc: str) -> bool:
    """Simulate dropping ``disc`` in ``col``; True if it makes a 4-in-a-row."""
    row = drop(grid, col, disc)
    if row is None:
        return False
    won = winner(grid) == disc
    grid[row][col] = EMPTY
    return won


def best_col(grid: list[list[str]], me: str, you: str) -> int:
    """AI column choice: win if able, else block your win, else prefer the center."""
    legal = [c for c in range(COLS) if not col_full(grid, c)]
    if not legal:
        return -1
    # 1. Take an immediate win.
    for c in legal:
        if _wins_with(grid, c, me):
            return c
    # 2. Block the opponent's immediate win.
    for c in legal:
        if _wins_with(grid, c, you):
            return c
    # 3. Avoid handing the opponent a win on their next move, if possible.
    safe = []
    for c in legal:
        row = drop(grid, c, me)
        gives_win = any(_wins_with(grid, c2, you) for c2 in range(COLS) if not col_full(grid, c2))
        grid[row][c] = EMPTY
        if not gives_win:
            safe.append(c)
    pool = safe or legal
    # 4. Prefer central columns (with a little randomness among equals).
    center = (COLS - 1) / 2
    best_dist = min(abs(c - center) for c in pool)
    central = [c for c in pool if abs(c - center) == best_dist]
    return random.choice(central)


def _render(console: Console, grid: list[list[str]], you: str, panda: str) -> None:
    nums = " ".join(f"[{MUTE}]{c + 1}[/{MUTE}]" for c in range(COLS))
    console.print(f"   {nums}")
    for row in grid:
        cells = []
        for cell in row:
            if cell == you:
                cells.append("🔴")
            elif cell == panda:
                cells.append("🟡")
            else:
                cells.append("[grey30]·[/grey30]")
        console.print("  " + " ".join(cells))
    console.print()


def _play(console: Console) -> None:
    header(console, GAME)
    grid = new_grid()
    you, panda = "R", "Y"
    console.print(f"  [{MUTE}]You're 🔴, the panda is 🟡. Type a column 1-7. (q to quit)[/{MUTE}]\n")
    _render(console, grid, you, panda)
    while True:
        raw = ask_line(console, "column 1-7:")
        if raw.lower() in ("q", "quit", ""):
            console.print(f"\n  [{MUTE}]gg![/{MUTE}]")
            return
        if not raw.isdigit() or not (1 <= int(raw) <= COLS):
            console.print(f"  [{WARN}]pick a column 1-7[/{WARN}]")
            continue
        col = int(raw) - 1
        if col_full(grid, col):
            console.print(f"  [{WARN}]that column is full — try another[/{WARN}]")
            continue
        drop(grid, col, you)
        if winner(grid) is None and not board_full(grid):
            pc = best_col(grid, panda, you)
            if pc >= 0:
                drop(grid, pc, panda)
        _render(console, grid, you, panda)
        w = winner(grid)
        if w == you:
            console.print(f"  [bold {GOOD}]🎉 Four in a row — you beat the panda![/bold {GOOD}]")
            return
        if w == panda:
            console.print(f"  [bold {ERROR}]🐼 The panda connects four. ronin wins.[/bold {ERROR}]")
            return
        if board_full(grid):
            console.print(f"  [bold {WARN}]🤝 Board full — it's a draw.[/bold {WARN}]")
            return


GAME = GameMeta(key="connect4", name="Connect Four", emoji="🔴",
                desc="drop discs, get 4 in a row vs the panda", play=_play)
