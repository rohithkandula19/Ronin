"""Connect Four vs ronin — drop discs on a 7-wide x 6-tall board and try to land
four in a row before the panda does.

The *rules* live in pure, I/O-free functions (``drop``, ``winner``, ``best_col``)
so they can be unit-tested without a terminal. ``_play`` drives a full-screen,
arrow-key-driven loop via the real-time engine: slide a column selector left/right
and press space/enter to drop your disc (you watch it fall). The panda's AI takes
any immediate win, blocks your immediate win, avoids handing you a win, and
otherwise leans toward the center. The winning four-in-a-row is highlighted.

If prompt_toolkit / raw mode can't set up a real terminal (headless, piped stdin),
we fall back to a typed "column 1-7" prompt instead of crashing.
"""
from __future__ import annotations

import random
import time

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# Rich palette (used only outside raw mode).
ACCENT = "#2dd4bf"
GOOD = "#9ece6a"
WARN = "#e0af68"
ERROR = "#f7768e"
MUTE = "#6b7089"

COLS = 7
ROWS = 6
EMPTY = " "

# Grid representation: ``grid`` is a list of ROWS rows, each a list of COLS cells.
# ``grid[0]`` is the TOP row, ``grid[ROWS - 1]`` is the BOTTOM row. Each cell is
# one of " " (empty), "R" (you / red) or "Y" (panda / amber). A disc dropped into
# a column comes to rest in the lowest empty row.


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


def _line_cells(grid: list[list[str]], r: int, c: int, dr: int, dc: int):
    """Return the 4 (row, col) coordinates of a length-4 line, or None if off-board."""
    cells = []
    for i in range(4):
        rr, cc = r + i * dr, c + i * dc
        if not (0 <= rr < ROWS and 0 <= cc < COLS):
            return None
        cells.append((rr, cc))
    return cells


def winning_line(grid: list[list[str]]):
    """Return the list of 4 cells forming a 4-in-a-row, or None."""
    for r in range(ROWS):
        for c in range(COLS):
            disc = grid[r][c]
            if disc == EMPTY:
                continue
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                cells = _line_cells(grid, r, c, dr, dc)
                if cells and all(grid[rr][cc] == disc for rr, cc in cells):
                    return cells
    return None


def winner(grid: list[list[str]]) -> str | None:
    """Return the disc char of any 4-in-a-row (horizontal/vertical/both diagonals)."""
    line = winning_line(grid)
    if line is None:
        return None
    r, c = line[0]
    return grid[r][c]


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


# ---------------------------------------------------------------------------
# Real-time rendering (raw ANSI; Rich markup does NOT render in raw mode)
# ---------------------------------------------------------------------------
_C_RED = "\x1b[38;2;247;118;142m"     # your disc
_C_AMBER = "\x1b[38;2;224;175;104m"   # panda disc
_C_TEAL = "\x1b[1;38;2;45;212;191m"   # accents / highlight
_C_MUTE = "\x1b[38;2;107;112;137m"    # frame / hints
_C_WIN = "\x1b[1;38;2;158;206;106m"   # winning-line glow
_RESET = "\x1b[0m"

YOU, PANDA = "R", "Y"


def _disc(cell: str, *, win: bool = False) -> str:
    """Render a single board cell as a coloured glyph."""
    if cell == YOU:
        col = _C_WIN if win else _C_RED
        return f"{col}●{_RESET}"
    if cell == PANDA:
        col = _C_WIN if win else _C_AMBER
        return f"{col}●{_RESET}"
    return f"{_C_MUTE}·{_RESET}"


def _frame(grid: list[list[str]], sel: int, *, status: str,
           win_cells=None, falling: tuple[int, int, str] | None = None) -> str:
    """Build the full-screen board frame as a single string.

    ``sel`` is the selected column. ``win_cells`` (set of (r, c)) glows. ``falling``
    is an optional (row, col, disc) drawn as a disc mid-flight while the rest of its
    column above it stays empty.
    """
    win_cells = win_cells or set()
    lines: list[str] = []
    lines.append(f"  {_C_TEAL}🔴 Connect Four{_RESET}   "
                 f"{_C_MUTE}you {_C_RED}●{_C_MUTE} · panda {_C_AMBER}●{_C_MUTE}{_RESET}")
    lines.append("")

    # Column-selector row: a ▼ marker over the chosen column.
    marker = ["   "]
    for c in range(COLS):
        if c == sel:
            full = col_full(grid, c)
            col = _C_MUTE if full else _C_TEAL
            marker.append(f"{col}▼{_RESET} ")
        else:
            marker.append("  ")
    lines.append("".join(marker))

    # Top border.
    lines.append(f"  {_C_MUTE}┌" + "──" * COLS + "┐" + _RESET)

    fr, fc, fdisc = (falling if falling else (-1, -1, EMPTY))
    for r in range(ROWS):
        row = [f"  {_C_MUTE}│{_RESET}"]
        for c in range(COLS):
            if falling and r == fr and c == fc:
                glyph = _disc(fdisc)
            elif falling and c == fc and r < fr and grid[r][c] == EMPTY:
                glyph = f"{_C_MUTE}·{_RESET}"  # leave space above the falling disc
            else:
                glyph = _disc(grid[r][c], win=(r, c) in win_cells)
            row.append(glyph + " ")
        row.append(f"{_C_MUTE}│{_RESET}")
        lines.append("".join(row))

    # Bottom border + column numbers.
    lines.append(f"  {_C_MUTE}└" + "──" * COLS + "┘" + _RESET)
    nums = ["   "]
    for c in range(COLS):
        col = _C_TEAL if c == sel else _C_MUTE
        nums.append(f"{col}{c + 1}{_RESET} ")
    lines.append("".join(nums))

    lines.append("")
    lines.append("  " + status)
    return "\n".join(lines)


def _animate_drop(rt, grid, sel, col, disc, *, base_status):
    """Repaint the frame with the disc visibly falling down ``col`` to its landing row."""
    landing = None
    for row in range(ROWS - 1, -1, -1):
        if grid[row][col] == EMPTY:
            landing = row
            break
    if landing is None:
        return None
    for row in range(landing + 1):
        rt.draw(_frame(grid, sel, status=base_status, falling=(row, col, disc)))
        time.sleep(0.045)
    grid[landing][col] = disc
    return landing


def _play(console: Console) -> None:
    header(console, GAME)
    from . import _realtime as rt

    if not rt.is_interactive():
        _play_fallback(console)
        return

    grid = new_grid()
    sel = COLS // 2

    hint = (f"{_C_MUTE}← → move · space/enter drop · q quits{_RESET}")
    quit_now = False
    result = None  # "you" / "panda" / "draw"
    win_cells: set = set()

    rt.enter_fullscreen()
    try:
        with rt.raw_mode():
            rt.draw(_frame(grid, sel, status=hint))
            while True:
                key = rt.read_key()
                if key in ("q", "esc"):
                    quit_now = True
                    break
                if key == "left":
                    sel = max(0, sel - 1)
                    rt.draw(_frame(grid, sel, status=hint))
                    continue
                if key == "right":
                    sel = min(COLS - 1, sel + 1)
                    rt.draw(_frame(grid, sel, status=hint))
                    continue
                if key not in ("space", "enter"):
                    continue
                if col_full(grid, sel):
                    rt.draw(_frame(grid, sel,
                                   status=f"{_C_AMBER}column {sel + 1} is full — pick another{_RESET}"))
                    continue

                # Your drop (animated).
                _animate_drop(rt, grid, sel, sel, YOU, base_status=hint)
                line = winning_line(grid)
                if line:
                    win_cells = set(line)
                    result = "you"
                    break
                if board_full(grid):
                    result = "draw"
                    break

                # Panda thinks, then drops (animated).
                rt.draw(_frame(grid, sel, status=f"{_C_AMBER}🐼 the panda is thinking…{_RESET}"))
                time.sleep(0.35)
                pc = best_col(grid, PANDA, YOU)
                if pc >= 0:
                    sel = pc
                    _animate_drop(rt, grid, sel, pc, PANDA, base_status=hint)
                line = winning_line(grid)
                if line:
                    win_cells = set(line)
                    result = "panda"
                    break
                if board_full(grid):
                    result = "draw"
                    break
                rt.draw(_frame(grid, sel, status=hint))

            # Final screen — paint the result with the winning line highlighted.
            if result == "you":
                end = f"{_C_WIN}🎉 four in a row — you beat the panda!{_RESET}  {_C_MUTE}(any key){_RESET}"
            elif result == "panda":
                end = f"{_C_RED}🐼 the panda connects four. ronin wins.{_RESET}  {_C_MUTE}(any key){_RESET}"
            elif result == "draw":
                end = f"{_C_AMBER}🤝 board full — it's a draw.{_RESET}  {_C_MUTE}(any key){_RESET}"
            else:
                end = None
            if end is not None:
                rt.draw(_frame(grid, sel, status=end, win_cells=win_cells))
                rt.read_key()
    finally:
        rt.exit_fullscreen()

    if quit_now:
        console.print(f"  [{MUTE}]gg![/{MUTE}]\n")
    elif result == "you":
        console.print(f"  [bold {GOOD}]🎉 Four in a row — you beat the panda![/bold {GOOD}]\n")
    elif result == "panda":
        console.print(f"  [bold {ERROR}]🐼 The panda connects four. ronin wins.[/bold {ERROR}]\n")
    elif result == "draw":
        console.print(f"  [bold {WARN}]🤝 Board full — it's a draw.[/bold {WARN}]\n")


def _render_plain(console: Console, grid: list[list[str]]) -> None:
    """Rich-markup board for the typed-prompt fallback (non-interactive)."""
    nums = " ".join(f"[{MUTE}]{c + 1}[/{MUTE}]" for c in range(COLS))
    console.print(f"   {nums}")
    win = set(winning_line(grid) or [])
    for r, row in enumerate(grid):
        cells = []
        for c, cell in enumerate(row):
            if cell == YOU:
                cells.append(f"[bold {GOOD if (r, c) in win else ERROR}]●[/]")
            elif cell == PANDA:
                cells.append(f"[bold {GOOD if (r, c) in win else WARN}]●[/]")
            else:
                cells.append("[grey30]·[/grey30]")
        console.print("  " + " ".join(cells))
    console.print()


def _play_fallback(console: Console) -> None:
    """Typed 'column 1-7' loop for non-TTY environments (never uses raw mode)."""
    grid = new_grid()
    console.print(f"  [{MUTE}]You're red ●, the panda is amber ●. Type a column 1-7. (q to quit)[/{MUTE}]\n")
    _render_plain(console, grid)
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
        drop(grid, col, YOU)
        if winner(grid) is None and not board_full(grid):
            pc = best_col(grid, PANDA, YOU)
            if pc >= 0:
                drop(grid, pc, PANDA)
        _render_plain(console, grid)
        w = winner(grid)
        if w == YOU:
            console.print(f"  [bold {GOOD}]🎉 Four in a row — you beat the panda![/bold {GOOD}]")
            return
        if w == PANDA:
            console.print(f"  [bold {ERROR}]🐼 The panda connects four. ronin wins.[/bold {ERROR}]")
            return
        if board_full(grid):
            console.print(f"  [bold {WARN}]🤝 Board full — it's a draw.[/bold {WARN}]")
            return


GAME = GameMeta(key="connect4", name="Connect Four", emoji="🔴",
                desc="drop discs, get 4 in a row vs the panda", play=_play)
