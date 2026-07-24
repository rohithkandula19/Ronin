"""Tetris — stack falling tetrominoes, clear lines, don't top out (real-time).

The *rules* live in pure, I/O-free functions (:func:`rotate`, :func:`collides`,
:func:`clear_lines`) so they can be unit-tested without a terminal. ``_play``
drives a real-time loop via the shared :mod:`._realtime` plumbing: raw-mode key
capture, a gravity tick that gets faster with level, and an in-place repaint. If
there is no real terminal (headless / piped stdin) we print a friendly message
rather than crashing.

Board representation
--------------------
The board is a ``list`` of rows, top-to-bottom, ``HEIGHT`` (20) rows tall and
``WIDTH`` (10) wide. Each cell is either ``None`` (empty) or a single-character
*piece key* string (one of "IOTSZJL") naming the colour of the locked block
sitting there. ``board[y][x]`` with the origin at the top-left.

A *piece* is described by its key plus a list of ``(x, y)`` cell offsets relative
to a piece-local origin. :func:`rotate` rotates such a cell list 90° clockwise.
``collides(board, cells, offset)`` translates ``cells`` by ``offset`` and reports
whether any resulting cell leaves the board or lands on a filled cell.
"""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, header

# Palette (Rich markup — used for the non-fullscreen messages only)
WARN = "#e0af68"
ERROR = "#f7768e"
MUTE = "#6b7089"

WIDTH = 10
HEIGHT = 20

# Tetromino definitions: key -> list of (x, y) cell offsets (spawn orientation).
SHAPES: dict[str, list[tuple[int, int]]] = {
    "I": [(0, 1), (1, 1), (2, 1), (3, 1)],
    "O": [(1, 0), (2, 0), (1, 1), (2, 1)],
    "T": [(1, 0), (0, 1), (1, 1), (2, 1)],
    "S": [(1, 0), (2, 0), (0, 1), (1, 1)],
    "Z": [(0, 0), (1, 0), (1, 1), (2, 1)],
    "J": [(0, 0), (0, 1), (1, 1), (2, 1)],
    "L": [(2, 0), (0, 1), (1, 1), (2, 1)],
}

# Raw ANSI truecolor per piece (Rich markup does NOT render in raw fullscreen).
_COLORS: dict[str, str] = {
    "I": "\x1b[38;2;125;211;252m",
    "O": "\x1b[38;2;224;175;104m",
    "T": "\x1b[38;2;186;130;230m",
    "S": "\x1b[38;2;158;206;106m",
    "Z": "\x1b[38;2;247;118;142m",
    "J": "\x1b[38;2;100;150;245m",
    "L": "\x1b[38;2;240;140;90m",
}
_MUTE = "\x1b[38;2;107;112;137m"
_ACCENT = "\x1b[1;38;2;45;212;191m"
_RESET = "\x1b[0m"

_CELL = "██"   # two chars wide so blocks look square
_EMPTY = "  "

# Score awarded per simultaneous line clear (classic-style), scaled by level.
_LINE_SCORE = {0: 0, 1: 100, 2: 300, 3: 500, 4: 800}


# ---------------------------------------------------------------------------
# Pure rules (no I/O)
# ---------------------------------------------------------------------------
def rotate(shape: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Rotate a piece's cell list 90° clockwise about its bounding box.

    ``shape`` is a list of ``(x, y)`` offsets. Clockwise rotation maps
    ``(x, y) -> (maxY - y, x)`` where ``maxY`` is the tallest row index, which
    keeps the rotated cells in non-negative coordinates. Applying this four
    times round-trips back to the original shape (possibly reordered).
    """
    if not shape:
        return []
    max_y = max(y for _, y in shape)
    return [(max_y - y, x) for (x, y) in shape]


def collides(
    board: list[list[str | None]],
    shape_cells: list[tuple[int, int]],
    offset: tuple[int, int],
) -> bool:
    """True if placing ``shape_cells`` at ``offset`` is illegal.

    Each cell ``(cx, cy)`` is translated by ``offset = (ox, oy)`` to an absolute
    board position. A collision is any cell that falls outside the board (a wall
    or the floor) or onto an already-filled cell. Cells above the top edge
    (negative ``y``) are tolerated so pieces can spawn/rotate partly off-screen.
    """
    ox, oy = offset
    for cx, cy in shape_cells:
        x = cx + ox
        y = cy + oy
        if x < 0 or x >= WIDTH or y >= HEIGHT:
            return True
        if y >= 0 and board[y][x] is not None:
            return True
    return False


def clear_lines(
    board: list[list[str | None]],
) -> tuple[list[list[str | None]], int]:
    """Remove every fully-filled row and return ``(new_board, num_cleared)``.

    A row is full when no cell is ``None``. Surviving rows keep their order and
    fall to the bottom; the cleared rows are replaced by empty rows pushed in at
    the top, so the returned board is always ``HEIGHT`` rows tall.
    """
    kept = [row for row in board if any(cell is None for cell in row)]
    cleared = HEIGHT - len(kept)
    new_rows = [[None] * WIDTH for _ in range(cleared)]
    return new_rows + kept, cleared


# ---------------------------------------------------------------------------
# Interactive helpers
# ---------------------------------------------------------------------------
def _empty_board() -> list[list[str | None]]:
    return [[None] * WIDTH for _ in range(HEIGHT)]


class _Bag:
    """7-bag randomiser: deals each tetromino once per cycle for fair pieces."""

    def __init__(self) -> None:
        self._bag: list[str] = []

    def next(self) -> str:
        if not self._bag:
            self._bag = list(SHAPES.keys())
            random.shuffle(self._bag)
        return self._bag.pop()


def _spawn(key: str) -> tuple[str, list[tuple[int, int]], tuple[int, int]]:
    """Return a fresh piece (key, cells, offset) centred at the top."""
    cells = list(SHAPES[key])
    return key, cells, (3, 0)


def _level_tick(level: int) -> float:
    """Seconds between gravity steps; faster as the level climbs."""
    return max(0.05, 0.55 - (level - 1) * 0.045)


def _drop_offset(
    board: list[list[str | None]],
    cells: list[tuple[int, int]],
    offset: tuple[int, int],
) -> tuple[int, int]:
    """Lowest legal offset straight down from ``offset`` (hard-drop target)."""
    ox, oy = offset
    while not collides(board, cells, (ox, oy + 1)):
        oy += 1
    return (ox, oy)


def _lock(
    board: list[list[str | None]],
    key: str,
    cells: list[tuple[int, int]],
    offset: tuple[int, int],
) -> None:
    """Stamp a piece's cells into the board (mutates ``board``)."""
    ox, oy = offset
    for cx, cy in cells:
        x, y = cx + ox, cy + oy
        if 0 <= y < HEIGHT and 0 <= x < WIDTH:
            board[y][x] = key


def _frame(
    board: list[list[str | None]],
    key: str,
    cells: list[tuple[int, int]],
    offset: tuple[int, int],
    next_key: str,
    *,
    score: int,
    level: int,
    lines: int,
    over: bool,
) -> str:
    # Overlay the active piece + its ghost onto a render grid.
    grid: list[list[str | None]] = [row[:] for row in board]
    ghost = _drop_offset(board, cells, offset)
    gx, gy = ghost
    ghost_set = {(cx + gx, cy + gy) for cx, cy in cells}
    ox, oy = offset
    active_set = {(cx + ox, cy + oy) for cx, cy in cells}

    out: list[str] = []
    out.append(f"  {_ACCENT}🟦 Tetris{_RESET}")
    out.append("")

    # Build the board rows next to a side panel.
    panel = _side_panel(next_key, score=score, level=level, lines=lines)
    top = "  ╔" + "══" * WIDTH + "╗"
    out.append(top + ("   " + panel[0] if len(panel) > 0 else ""))
    for y in range(HEIGHT):
        cells_str = []
        for x in range(WIDTH):
            pos = (x, y)
            if pos in active_set:
                cells_str.append(f"{_COLORS[key]}{_CELL}{_RESET}")
            elif grid[y][x] is not None:
                ck = grid[y][x]
                cells_str.append(f"{_COLORS[ck]}{_CELL}{_RESET}")
            elif pos in ghost_set:
                cells_str.append(f"{_MUTE}··{_RESET}")
            else:
                cells_str.append(_EMPTY)
        row = "  ║" + "".join(cells_str) + "║"
        side = panel[y + 1] if y + 1 < len(panel) else ""
        out.append(row + ("   " + side if side else ""))
    out.append("  ╚" + "══" * WIDTH + "╝")
    out.append("")
    if over:
        out.append(f"  {_COLORS['Z']}game over{_RESET} {_MUTE}— press any key{_RESET}")
    else:
        out.append(
            f"  {_MUTE}← → move · ↑ rotate · ↓ soft · space hard · q quit{_RESET}"
        )
    return "\n".join(out)


def _side_panel(next_key: str, *, score: int, level: int, lines: int) -> list[str]:
    """Lines for the info/preview column rendered to the right of the board."""
    rows: list[str] = []
    rows.append(f"{_MUTE}score{_RESET}")
    rows.append(f"{_ACCENT}{score}{_RESET}")
    rows.append("")
    rows.append(f"{_MUTE}level{_RESET}  {_ACCENT}{level}{_RESET}")
    rows.append(f"{_MUTE}lines{_RESET}  {_ACCENT}{lines}{_RESET}")
    rows.append("")
    rows.append(f"{_MUTE}next{_RESET}")
    # 4x2 preview grid for the next piece.
    cells = set(SHAPES[next_key])
    for py in range(2):
        line = ""
        for px in range(4):
            if (px, py) in cells:
                line += f"{_COLORS[next_key]}{_CELL}{_RESET}"
            else:
                line += "  "
        rows.append(line)
    return rows


def _play(console: Console) -> None:
    header(console, GAME)
    from . import _realtime as rt

    if not rt.is_interactive():
        console.print(
            f"  [{WARN}]Tetris needs a real terminal — run it directly "
            f"in your shell 🟦[/{WARN}]\n"
        )
        return

    board = _empty_board()
    bag = _Bag()
    key, cells, offset = _spawn(bag.next())
    next_key = bag.next()
    score = 0
    lines_total = 0
    level = 1
    over = False
    quit_now = False
    accum = 0.0  # accumulated time toward the next gravity step

    def _lines_to_level(n: int) -> int:
        return n // 10 + 1

    rt.enter_fullscreen()
    try:
        with rt.raw_mode():
            while True:
                rt.draw(
                    _frame(
                        board, key, cells, offset, next_key,
                        score=score, level=level, lines=lines_total, over=over,
                    )
                )
                if over:
                    rt.read_key()
                    break

                tick = _level_tick(level)
                key_in = rt.poll_key(min(tick, 0.05))
                if key_in in ("q", "esc"):
                    quit_now = True
                    break

                hard = False
                if key_in == "left":
                    if not collides(board, cells, (offset[0] - 1, offset[1])):
                        offset = (offset[0] - 1, offset[1])
                elif key_in == "right":
                    if not collides(board, cells, (offset[0] + 1, offset[1])):
                        offset = (offset[0] + 1, offset[1])
                elif key_in == "up":
                    rotated = rotate(cells)
                    # Wall-kick: try in place, then nudge horizontally.
                    for kick in (0, -1, 1, -2, 2):
                        cand = (offset[0] + kick, offset[1])
                        if not collides(board, rotated, cand):
                            cells = rotated
                            offset = cand
                            break
                elif key_in == "down":
                    if not collides(board, cells, (offset[0], offset[1] + 1)):
                        offset = (offset[0], offset[1] + 1)
                        score += 1
                    accum = 0.0
                elif key_in == "space":
                    target = _drop_offset(board, cells, offset)
                    score += (target[1] - offset[1]) * 2
                    offset = target
                    hard = True

                # Advance gravity by elapsed time.
                accum += min(tick, 0.05)
                step = hard or accum >= tick
                if step:
                    accum = 0.0
                    if not collides(board, cells, (offset[0], offset[1] + 1)):
                        offset = (offset[0], offset[1] + 1)
                    else:
                        # Land the piece, clear lines, spawn the next.
                        _lock(board, key, cells, offset)
                        board, cleared = clear_lines(board)
                        if cleared:
                            lines_total += cleared
                            score += _LINE_SCORE.get(cleared, 0) * level
                            level = _lines_to_level(lines_total)
                        key, cells, offset = _spawn(next_key)
                        next_key = bag.next()
                        if collides(board, cells, offset):
                            over = True
    except Exception:  # noqa: BLE001 - never crash the terminal session
        pass
    finally:
        rt.exit_fullscreen()

    if quit_now:
        console.print(f"  [{MUTE}]bye — score {score}, {lines_total} lines[/{MUTE}]\n")
    else:
        console.print(
            f"  [bold {ERROR}]game over[/bold {ERROR}] "
            f"[{MUTE}]final score {score} · {lines_total} lines · level {level}[/{MUTE}]\n"
        )


GAME = GameMeta(
    key="tetris",
    name="Tetris",
    emoji="🟦",
    desc="stack, clear lines, don't top out",
    play=_play,
)
