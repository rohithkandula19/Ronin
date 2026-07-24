"""Sudoku — fill the 9x9 grid so every row, column and 3x3 box holds 1-9.

The *rules* live in pure, I/O-free functions (``is_valid``, ``is_solved``,
``generate``) so they can be unit-tested without a terminal. ``generate`` builds
a fully-solved grid via randomized backtracking, then carves out cells one at a
time, keeping only removals that preserve a *unique* solution — so every puzzle
is real and solvable to exactly one answer. ``_play`` drives a full-screen,
arrow-key board via ``_realtime``; on a non-TTY it falls back to a typed
"row col val" prompt rather than touching raw mode.
"""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

N = 9
BOX = 3
EMPTY = 0


# ---------------------------------------------------------------------------
# Pure rules (no I/O) — tests depend on these names/signatures.
# ---------------------------------------------------------------------------
def is_valid(board: list[list[int]], r: int, c: int, val: int) -> bool:
    """True if ``val`` (1-9) may sit at ``board[r][c]`` without breaking the
    row, column or 3x3 box — *ignoring* whatever currently occupies (r, c)."""
    if val == EMPTY:
        return True
    for i in range(N):
        if i != c and board[r][i] == val:
            return False
        if i != r and board[i][c] == val:
            return False
    br, bc = (r // BOX) * BOX, (c // BOX) * BOX
    for i in range(br, br + BOX):
        for j in range(bc, bc + BOX):
            if (i != r or j != c) and board[i][j] == val:
                return False
    return True


def is_solved(board: list[list[int]]) -> bool:
    """True only if the grid is completely filled (no zeros) and every filled
    cell is consistent with the Sudoku constraints."""
    for r in range(N):
        for c in range(N):
            v = board[r][c]
            if v == EMPTY or not is_valid(board, r, c, v):
                return False
    return True


def _find_empty(board: list[list[int]]) -> tuple[int, int] | None:
    for r in range(N):
        for c in range(N):
            if board[r][c] == EMPTY:
                return (r, c)
    return None


def _solve(board: list[list[int]], rng: random.Random | None = None) -> bool:
    """Fill ``board`` in place by backtracking. With ``rng`` the digit order is
    shuffled (used to generate a random full grid); without it the order is
    fixed (deterministic completion)."""
    spot = _find_empty(board)
    if spot is None:
        return True
    r, c = spot
    digits = list(range(1, N + 1))
    if rng is not None:
        rng.shuffle(digits)
    for v in digits:
        if is_valid(board, r, c, v):
            board[r][c] = v
            if _solve(board, rng):
                return True
            board[r][c] = EMPTY
    return False


def _count_solutions(board: list[list[int]], limit: int = 2) -> int:
    """Count solutions of ``board`` up to ``limit`` (stops early). Used to test
    whether a puzzle has a *unique* answer (limit=2 ⇒ "1" means unique)."""
    spot = _find_empty(board)
    if spot is None:
        return 1
    r, c = spot
    total = 0
    for v in range(1, N + 1):
        if is_valid(board, r, c, v):
            board[r][c] = v
            total += _count_solutions(board, limit)
            board[r][c] = EMPTY
            if total >= limit:
                break
    return total


def generate(rng: random.Random, holes: int = 40) -> tuple[list[list[int]], list[list[int]]]:
    """Return ``(puzzle, solution)`` — both 9x9 lists of ints, 0 = blank.

    A full valid solution is built first, then cells are removed one at a time
    (in random order) only when removal keeps the solution *unique*. ``holes``
    is the target number of blanks (higher ⇒ harder); the actual count may be a
    touch lower if uniqueness can't be preserved. Deterministic for a given
    ``rng`` state.
    """
    solution = [[EMPTY] * N for _ in range(N)]
    _solve(solution, rng)

    puzzle = [row[:] for row in solution]
    cells = [(r, c) for r in range(N) for c in range(N)]
    rng.shuffle(cells)
    holes = max(0, min(holes, N * N - 1))
    removed = 0
    for r, c in cells:
        if removed >= holes:
            break
        saved = puzzle[r][c]
        puzzle[r][c] = EMPTY
        # Removal is safe only if exactly one solution remains.
        work = [row[:] for row in puzzle]
        if _count_solutions(work, limit=2) == 1:
            removed += 1
        else:
            puzzle[r][c] = saved
    return puzzle, solution


# ---------------------------------------------------------------------------
# Raw ANSI palette (Rich markup does NOT render in raw/fullscreen mode).
# ---------------------------------------------------------------------------
_TEAL = "\x1b[38;2;45;212;191m"
_SKY = "\x1b[38;2;125;211;252m"
_GREEN = "\x1b[38;2;158;206;106m"
_ROSE = "\x1b[38;2;247;118;142m"
_MUTE = "\x1b[38;2;107;112;137m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"
# cursor cell: teal background, black foreground
_CURSOR = "\x1b[48;2;45;212;191m\x1b[38;2;0;0;0m"


def _conflicts(board: list[list[int]]) -> set[tuple[int, int]]:
    """Set of cells whose value clashes with another in the same row/col/box."""
    bad: set[tuple[int, int]] = set()
    for r in range(N):
        for c in range(N):
            v = board[r][c]
            if v != EMPTY and not is_valid(board, r, c, v):
                bad.add((r, c))
    return bad


def _frame(board: list[list[int]], given: list[list[bool]], cur: tuple[int, int],
           *, status: str, won: bool) -> str:
    bad = _conflicts(board)
    filled = sum(1 for r in range(N) for c in range(N) if board[r][c] != EMPTY)
    lines = [
        f"  {_TEAL}{_BOLD}\U0001f522 Sudoku{_RESET}   "
        f"{_MUTE}filled {filled}/81{_RESET}   "
        f"{_ROSE if bad else _MUTE}conflicts {len(bad)}{_RESET}",
        "",
    ]
    top = f"   {_MUTE}┏━━━━━━━┳━━━━━━━┳━━━━━━━┓{_RESET}"
    mid = f"   {_MUTE}┣━━━━━━━╋━━━━━━━╋━━━━━━━┫{_RESET}"
    bot = f"   {_MUTE}┗━━━━━━━┻━━━━━━━┻━━━━━━━┛{_RESET}"
    lines.append(top)
    for r in range(N):
        cells = [f"   {_MUTE}┃{_RESET}"]
        for c in range(N):
            v = board[r][c]
            glyph = str(v) if v != EMPTY else "·"
            here = (r, c) == cur
            if here:
                colour = _CURSOR + _BOLD
            elif v == EMPTY:
                colour = _MUTE
            elif (r, c) in bad:
                colour = _ROSE + _BOLD
            elif given[r][c]:
                colour = _SKY + _BOLD
            else:
                colour = _TEAL
            cells.append(f"{colour}{glyph}{_RESET}")
            # vertical box separators (heavier between 3x3 blocks)
            if c % BOX == BOX - 1:
                cells.append(f"{_MUTE}┃{_RESET}")
            else:
                cells.append(" ")
        lines.append(" ".join(cells))
        if r % BOX == BOX - 1 and r != N - 1:
            lines.append(mid)
    lines.append(bot)
    lines.append("")
    if status:
        lines.append("  " + status)
    elif won:
        lines.append(f"  {_GREEN}{_BOLD}\U0001f3c6 Solved! press any key{_RESET}")
    else:
        lines.append(f"  {_MUTE}arrows move · 1-9 fill · 0/⌫ clear · "
                     f"h hint · q quit{_RESET}")
    return "\n".join(lines)


def _hint(board: list[list[int]], solution: list[list[int]],
          given: list[list[bool]], cur: tuple[int, int]) -> tuple[int, int] | None:
    """Pick a cell to reveal: the cursor cell if it's blank/wrong, else the
    first such cell scanning forward. Returns the cell filled, or None if the
    board already matches the solution."""
    order = [cur] + [(r, c) for r in range(N) for c in range(N)]
    for r, c in order:
        if not given[r][c] and board[r][c] != solution[r][c]:
            board[r][c] = solution[r][c]
            return (r, c)
    return None


def _play(console: Console) -> None:
    header(console, GAME)
    from . import _realtime as rt

    rng = random.Random()
    puzzle, solution = generate(rng, holes=44)
    given = [[puzzle[r][c] != EMPTY for c in range(N)] for r in range(N)]

    if not rt.is_interactive():
        _play_fallback(console, puzzle, solution, given)
        return

    board = [row[:] for row in puzzle]
    cur = (0, 0)
    won = False
    quit_now = False

    rt.enter_fullscreen()
    try:
        with rt.raw_mode():
            while True:
                if not won and is_solved(board):
                    won = True
                rt.draw(_frame(board, given, cur, status="", won=won))
                if won:
                    rt.read_key()
                    break
                key = rt.read_key()
                if key in ("q", "esc"):
                    quit_now = True
                    break
                r, c = cur
                if key == "up":
                    cur = ((r - 1) % N, c)
                elif key == "down":
                    cur = ((r + 1) % N, c)
                elif key == "left":
                    cur = (r, (c - 1) % N)
                elif key == "right":
                    cur = (r, (c + 1) % N)
                elif key == "h":
                    _hint(board, solution, given, cur)
                elif key in ("0", "backspace", "delete"):
                    if not given[r][c]:
                        board[r][c] = EMPTY
                elif key in "123456789" and len(key) == 1:
                    if not given[r][c]:
                        board[r][c] = int(key)
    finally:
        rt.exit_fullscreen()

    if won:
        console.print("  [bold #9ece6a]\U0001f3c6 Solved — nicely done![/bold #9ece6a]\n")
    elif quit_now:
        filled = sum(1 for row in board for v in row if v != EMPTY)
        console.print(f"  [#6b7089]bye — filled {filled}/81[/#6b7089]\n")


# ---------------------------------------------------------------------------
# Non-interactive typed fallback (tests / piped stdin) — no raw mode.
# ---------------------------------------------------------------------------
def _render_plain(console: Console, board: list[list[int]],
                  given: list[list[bool]]) -> None:
    bad = _conflicts(board)
    console.print(f"  [dim]    {' '.join(str(c) for c in range(N))}[/dim]")
    console.print("  [dim]   ┏" + "━" * (N * 2 + 1) + "┓[/dim]")
    for r in range(N):
        cells = []
        for c in range(N):
            v = board[r][c]
            g = str(v) if v != EMPTY else "·"
            if v == EMPTY:
                cells.append(f"[dim]{g}[/dim]")
            elif (r, c) in bad:
                cells.append(f"[bold #f7768e]{g}[/bold #f7768e]")
            elif given[r][c]:
                cells.append(f"[bold #7dd3fc]{g}[/bold #7dd3fc]")
            else:
                cells.append(f"[#2dd4bf]{g}[/#2dd4bf]")
        console.print(f"  [dim]{r}  ┃[/dim] " + " ".join(cells) + " [dim]┃[/dim]")
    console.print("  [dim]   ┗" + "━" * (N * 2 + 1) + "┛[/dim]\n")


def _play_fallback(console: Console, puzzle: list[list[int]],
                   solution: list[list[int]], given: list[list[bool]]) -> None:
    board = [row[:] for row in puzzle]
    console.print("  [dim]Type \"3 4 7\" to put 7 at row 3 col 4 (0-8). "
                  "\"3 4 0\" clears. \"h\" hints, q quits.[/dim]\n")
    _render_plain(console, board, given)

    while True:
        if is_solved(board):
            console.print("  [bold #9ece6a]\U0001f3c6 Solved — nicely done![/bold #9ece6a]")
            return
        raw = ask_line(console, "row col val:")
        low = raw.lower()
        if low in ("q", "quit", ""):
            console.print("\n  [dim]gg![/dim]")
            return
        if low in ("h", "hint"):
            spot = _hint(board, solution, given, (0, 0))
            _render_plain(console, board, given)
            if spot is None:
                console.print("  [yellow]nothing left to hint[/yellow]")
            continue
        parts = low.split()
        if len(parts) != 3 or not all(p.lstrip("-").isdigit() for p in parts):
            console.print("  [yellow]format: row col val  (e.g. 3 4 7)[/yellow]")
            continue
        r, c, v = (int(p) for p in parts)
        if not (0 <= r < N and 0 <= c < N and 0 <= v <= 9):
            console.print(f"  [yellow]row/col 0-{N - 1}, val 0-9[/yellow]")
            continue
        if given[r][c]:
            console.print("  [yellow]that's a given clue — can't change it[/yellow]")
            continue
        board[r][c] = v
        _render_plain(console, board, given)


GAME = GameMeta(key="sudoku", name="Sudoku", emoji="\U0001f522",
                desc="fill the grid — every row, col & box 1-9", play=_play)
