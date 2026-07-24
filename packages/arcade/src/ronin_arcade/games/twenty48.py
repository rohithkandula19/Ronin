"""2048 — slide tiles on a 4x4 grid and try to reach 2048 (real-time).

The *rules* live in pure, I/O-free functions (``slide_left``, ``move``,
``has_moves``) so they can be unit-tested without a terminal. ``_play`` drives a
turn-based real-time loop via the shared ``_realtime`` plumbing: instant arrow
keys, a full-screen overlay, and an in-place repaint. If we can't get a real
terminal (headless / piped stdin) we fall back to a typed WASD prompt rather
than touching raw mode.
"""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header
from . import _realtime as rt

# Palette (Rich markup, used only outside raw mode)
ACCENT = "#2dd4bf"
INFO = "#7dd3fc"
GOOD = "#9ece6a"
WARN = "#e0af68"
ERROR = "#f7768e"
MUTE = "#6b7089"

SIZE = 4
WIN_TILE = 2048
CELL_W = 6  # fixed-width tile cell so the grid stays aligned


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
# Grid helpers
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


# ---------------------------------------------------------------------------
# Raw ANSI rendering (Rich markup does NOT render inside raw/full-screen mode)
# ---------------------------------------------------------------------------
_RESET = "\x1b[0m"
_C_TEAL = "\x1b[38;2;45;212;191m"
_C_GREEN = "\x1b[38;2;158;206;106m"
_C_ROSE = "\x1b[38;2;247;118;142m"
_C_AMBER = "\x1b[38;2;224;175;104m"
_C_SKY = "\x1b[38;2;125;211;252m"
_C_MUTE = "\x1b[38;2;107;112;137m"

# Per-value tile colour: 2,4 cool → 2048+ hot. Each is (fg_truecolor,).
_TILE_FG = {
    0: "\x1b[38;2;58;62;82m",       # empty dot, very dim
    2: "\x1b[1;38;2;125;211;252m",  # sky
    4: "\x1b[1;38;2;88;191;240m",   # lighter blue
    8: "\x1b[1;38;2;45;212;191m",   # teal
    16: "\x1b[1;38;2;94;214;164m",  # teal-green
    32: "\x1b[1;38;2;158;206;106m",  # green
    64: "\x1b[1;38;2;198;208;90m",  # green-amber
    128: "\x1b[1;38;2;224;175;104m",  # amber
    256: "\x1b[1;38;2;236;152;94m",  # orange
    512: "\x1b[1;38;2;247;118;142m",  # rose
    1024: "\x1b[1;38;2;255;96;120m",  # hot rose
    2048: "\x1b[1;38;2;255;215;0m",  # gold
}
_TILE_HOT = "\x1b[1;38;2;255;236;110m"  # > 2048


def _tile_color(value: int) -> str:
    return _TILE_FG.get(value, _TILE_HOT if value > 2048 else _C_MUTE)


def _cell(value: int) -> str:
    """A fixed-width coloured cell for one tile."""
    if value == 0:
        text = "·".center(CELL_W)
        return f"{_TILE_FG[0]}{text}{_RESET}"
    text = str(value).center(CELL_W)
    return f"{_tile_color(value)}{text}{_RESET}"


def _frame(grid: list[list[int]], score: int, *, won: bool, over: bool,
           best: int) -> str:
    inner = SIZE * (CELL_W + 1) - 1  # cells + interior separators
    top = "  ╭" + "─" * inner + "╮"
    bot = "  ╰" + "─" * inner + "╯"

    lines = [
        f"  {_C_TEAL}🔢 2048{_RESET}   {_C_MUTE}score{_RESET} "
        f"{_C_SKY}{score}{_RESET}   {_C_MUTE}best{_RESET} {_C_AMBER}{best}{_RESET}",
        "",
        top,
    ]
    for ri, row in enumerate(grid):
        cells = [_cell(v) for v in row]
        sep = f"{_C_MUTE}│{_RESET}"
        body = sep.join(cells)
        lines.append(f"  {_C_MUTE}│{_RESET}{body}{_C_MUTE}│{_RESET}")
        if ri < SIZE - 1:
            mid = "  " + _C_MUTE + "├" + (
                ("─" * CELL_W + "┼") * (SIZE - 1) + "─" * CELL_W
            ) + "┤" + _RESET
            lines.append(mid)
    lines.append(bot)
    lines.append("")

    if over:
        lines.append(
            f"  {_C_ROSE}game over{_RESET} {_C_MUTE}— no moves left · "
            f"press any key{_RESET}"
        )
    elif won:
        lines.append(
            f"  {_TILE_FG[2048]}you reached 2048! 🎉{_RESET} "
            f"{_C_MUTE}arrows keep going · q quits{_RESET}"
        )
    else:
        lines.append(f"  {_C_MUTE}arrows / wasd move · q quits{_RESET}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Interactive loop
# ---------------------------------------------------------------------------
_KEYS = {
    "up": "up", "down": "down", "left": "left", "right": "right",
    "w": "up", "a": "left", "s": "down", "d": "right",
}


def _play(console: Console) -> None:
    header(console, GAME)

    if not rt.is_interactive():
        _play_fallback(console)
        return

    grid = _new_grid()
    score = 0
    best = 0
    won = False
    over = False
    quit_now = False

    rt.enter_fullscreen()
    try:
        with rt.raw_mode():
            while True:
                best = max(best, max((v for row in grid for v in row), default=0))
                rt.draw(_frame(grid, score, won=won, over=over, best=best))

                if over:
                    rt.read_key()  # any key dismisses the final screen
                    break

                key = rt.read_key()
                if key in ("q", "esc"):
                    quit_now = True
                    break

                direction = _KEYS.get(key)
                if direction is None:
                    continue

                new_grid, gained = move(grid, direction)
                if new_grid == grid:
                    continue  # move changed nothing — ignore

                grid = new_grid
                score += gained
                _spawn(grid)

                if not won and _has_won(grid):
                    won = True
                if not has_moves(grid):
                    over = True
    finally:
        rt.exit_fullscreen()

    best = max(best, score)
    if quit_now:
        console.print(f"  [{MUTE}]bye — score {score} · best tile {best}[/{MUTE}]\n")
    elif won:
        console.print(f"  [bold {ACCENT}]nice — you hit 2048![/bold {ACCENT}] "
                      f"[{MUTE}]final score {score}[/{MUTE}]\n")
    else:
        console.print(f"  [bold {ERROR}]game over[/bold {ERROR}] "
                      f"[{MUTE}]final score {score}[/{MUTE}]\n")


def _play_fallback(console: Console) -> None:
    """Typed WASD prompt for non-TTY environments (never touches raw mode)."""
    console.print(f"  [{MUTE}]w=up  a=left  s=down  d=right   q=quit[/{MUTE}]\n")
    grid = _new_grid()
    score = 0
    won = False

    while True:
        _render_plain(console, grid, score)

        if not has_moves(grid):
            console.print(f"  [bold {ERROR}]game over[/bold {ERROR}] "
                          f"[{MUTE}]final score {score}[/{MUTE}]\n")
            return

        try:
            raw = ask_line(console, "move (wasd):").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print(f"\n  [{MUTE}]bye — score {score}[/{MUTE}]\n")
            return

        if raw in ("q", "quit"):
            console.print(f"  [{MUTE}]bye — score {score}[/{MUTE}]\n")
            return

        direction = _KEYS.get(raw[:1])
        if direction is None:
            console.print(f"  [{WARN}]use w/a/s/d (or q to quit)[/{WARN}]\n")
            continue

        new_grid, gained = move(grid, direction)
        if new_grid == grid:
            console.print(f"  [{MUTE}]no change — try another direction[/{MUTE}]\n")
            continue

        grid = new_grid
        score += gained
        _spawn(grid)

        if not won and _has_won(grid):
            won = True
            console.print(f"  [bold {ACCENT}]you reached 2048! 🎉[/bold {ACCENT}] "
                          f"[{MUTE}]keep going or q to quit[/{MUTE}]\n")


def _render_plain(console: Console, grid: list[list[int]], score: int) -> None:
    from rich.table import Table

    table = Table(show_header=False, box=None, padding=(0, 1), border_style=MUTE)
    for _ in range(SIZE):
        table.add_column(justify="right", width=6, no_wrap=True)
    for row in grid:
        cells = []
        for v in row:
            text = "·" if v == 0 else str(v)
            color = INFO if v < 128 else (WARN if v < WIN_TILE else ACCENT)
            cells.append(f"[bold {color}]{text:>5}[/bold {color}]")
        table.add_row(*cells)
    console.print(table)
    console.print(f"  [{MUTE}]score[/{MUTE}] [bold {INFO}]{score}[/bold {INFO}]\n")


GAME = GameMeta(
    key="2048",
    name="2048",
    emoji="🔢",
    desc="slide tiles, reach 2048",
    play=_play,
)
