"""Maze — find the exit before the clock judges you (real-time).

The *rules* live in pure, I/O-free functions (:func:`generate_maze`,
:func:`neighbors`, :func:`solve`) so they can be unit-tested without a terminal.
``_play`` drives a real-time loop via the shared :mod:`._realtime` plumbing:
raw-mode arrow-key capture, a live move/time counter, an in-place repaint, and a
hint that briefly flashes the way out. If there is no real terminal (headless /
piped stdin) we print a friendly message rather than crashing.

Maze representation
-------------------
A maze is a *perfect maze* (a spanning tree over the grid: every cell reachable,
exactly one simple path between any two cells, no loops). It is represented as an
**open-passage adjacency map**: a ``dict`` mapping each cell ``(x, y)`` to the
``set`` of orthogonally-adjacent cells it has an *open passage* to. Coordinates
are ``(x, y)`` with the origin at the top-left; ``x`` grows right, ``y`` grows
down. A wall stands between two adjacent cells exactly when neither lists the
other — so ``b in maze[a]`` means "you can walk from ``a`` to ``b``". This shape
is symmetric (``a in maze[b] <=> b in maze[a]``), trivial to render as
box-drawing walls, and is itself the graph that :func:`solve` runs BFS over.
"""
from __future__ import annotations

import random
import shutil
import time
from collections import deque

from rich.console import Console

from ._engine import GameMeta, ask_line, header
from . import _realtime as rt  # real-time arrow-key game

# Palette (Rich markup — used only for the non-fullscreen messages)
GOOD = "#9ece6a"
WARN = "#e0af68"
MUTE = "#6b7089"

# Direction name -> (dx, dy). Origin top-left; "up" decreases y.
_DIRS: dict[str, tuple[int, int]] = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

# Order in which the generator/neighbours probe around a cell (fixed → the maze
# is fully determined by the supplied random.Random's draw sequence).
_PROBE: tuple[tuple[int, int], ...] = ((0, -1), (0, 1), (-1, 0), (1, 0))


# ---------------------------------------------------------------------------
# Pure rules (no I/O)
# ---------------------------------------------------------------------------
def neighbors(cell: tuple[int, int], w: int, h: int) -> list[tuple[int, int]]:
    """Return the in-bounds orthogonal grid neighbours of ``cell``.

    These are the up-to-four cells sharing an edge with ``cell`` that lie inside
    ``[0, w) x [0, h)`` — *grid* adjacency, ignoring walls. The order is stable
    (up, down, left, right) so the generator is deterministic for a fixed RNG.
    """
    x, y = cell
    out: list[tuple[int, int]] = []
    for dx, dy in _PROBE:
        nx, ny = x + dx, y + dy
        if 0 <= nx < w and 0 <= ny < h:
            out.append((nx, ny))
    return out


def generate_maze(w: int, h: int, rng: random.Random) -> dict:
    """Build a random *perfect* maze with the recursive-backtracker (DFS) carver.

    Starting at ``(0, 0)`` we walk to a random unvisited neighbour, knocking the
    wall between as we go, and backtrack (via an explicit stack) when boxed in.
    Because every cell is visited exactly once and a passage is carved only on
    first discovery, the result is a spanning tree — a perfect maze that is
    always solvable. Returns the open-passage adjacency map documented in the
    module docstring. Deterministic for a fixed ``rng``.
    """
    passages: dict[tuple[int, int], set[tuple[int, int]]] = {
        (x, y): set() for x in range(w) for y in range(h)
    }
    if w <= 0 or h <= 0:
        return passages

    start = (0, 0)
    visited = {start}
    stack = [start]
    while stack:
        cell = stack[-1]
        unvisited = [n for n in neighbors(cell, w, h) if n not in visited]
        if not unvisited:
            stack.pop()
            continue
        nxt = rng.choice(unvisited)
        passages[cell].add(nxt)      # carve the wall both ways → symmetric
        passages[nxt].add(cell)
        visited.add(nxt)
        stack.append(nxt)
    return passages


def solve(
    maze: dict,
    start: tuple[int, int],
    end: tuple[int, int],
    w: int,
    h: int,
) -> list[tuple[int, int]]:
    """Shortest path from ``start`` to ``end`` through open passages (BFS).

    Walks the open-passage graph breadth-first and reconstructs the route,
    returning the list of cells ``[start, ..., end]`` inclusive. In a perfect
    maze every pair of cells is connected by exactly one simple path, so this
    always succeeds — which both guarantees the maze is winnable and powers the
    in-game hint. Returns ``[]`` only if ``end`` is genuinely unreachable.
    """
    if start == end:
        return [start]
    prev: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        if cur == end:
            break
        for nxt in sorted(maze.get(cur, ())):  # sorted → deterministic order
            if nxt not in prev:
                prev[nxt] = cur
                queue.append(nxt)
    if end not in prev:
        return []
    path: list[tuple[int, int]] = []
    node: tuple[int, int] | None = end
    while node is not None:
        path.append(node)
        node = prev[node]
    path.reverse()
    return path


# ---------------------------------------------------------------------------
# Rendering (ANSI truecolor — Rich markup does NOT render in raw fullscreen)
# ---------------------------------------------------------------------------
_C_WALL = "\x1b[38;2;88;91;112m"        # slate walls
_C_PLAYER = "\x1b[1;38;2;45;212;191m"   # teal @
_C_EXIT = "\x1b[1;38;2;158;206;106m"    # green goal
_C_HINT = "\x1b[38;2;125;211;252m"      # sky-blue hint dots
_C_DIM = "\x1b[38;2;107;112;137m"
_C_ACCENT = "\x1b[1;38;2;45;212;191m"
_RESET = "\x1b[0m"

# Box-drawing glyph for a wall junction, keyed by which arms are walls:
# (North, South, East, West) booleans.
_BOX: dict[tuple[bool, bool, bool, bool], str] = {
    (False, False, False, False): " ",
    (True, False, False, False): "╵",   # ╵
    (False, True, False, False): "╷",    # ╷
    (False, False, True, False): "╶",    # ╶
    (False, False, False, True): "╴",    # ╴
    (True, True, False, False): "│",     # │
    (False, False, True, True): "─",     # ─
    (False, True, True, False): "┌",     # ┌
    (False, True, False, True): "┐",     # ┐
    (True, False, True, False): "└",     # └
    (True, False, False, True): "┘",     # ┘
    (True, True, True, False): "├",      # ├
    (True, True, False, True): "┤",      # ┤
    (False, True, True, True): "┬",      # ┬
    (True, False, True, True): "┴",      # ┴
    (True, True, True, True): "┼",       # ┼
}


def _wall_grid(maze: dict, w: int, h: int) -> list[list[bool]]:
    """Whether each *edge* slot of the (2h+1)x(2w+1) render grid is a wall.

    Even/odd row+col slots alternate junction / edge / cell-interior. Only the
    edge slots are filled here (junctions are derived from their neighbouring
    edges at draw time); interiors are never walls.
    """
    gh, gw = 2 * h + 1, 2 * w + 1
    wall = [[False] * gw for _ in range(gh)]
    for gr in range(gh):
        for gc in range(gw):
            if gr % 2 == 0 and gc % 2 == 1:        # horizontal edge (cell top/bottom)
                x, jy = (gc - 1) // 2, gr // 2
                wall[gr][gc] = jy in (0, h) or (x, jy - 1) not in maze[(x, jy)]
            elif gr % 2 == 1 and gc % 2 == 0:      # vertical edge (cell left/right)
                y, jx = (gr - 1) // 2, gc // 2
                wall[gr][gc] = jx in (0, w) or (jx - 1, y) not in maze[(jx, y)]
    return wall


def _maze_lines(
    maze: dict,
    w: int,
    h: int,
    player: tuple[int, int],
    end: tuple[int, int],
    hint: list[tuple[int, int]] | None,
) -> list[str]:
    """Render the maze body as a list of (coloured) box-drawing lines."""
    gh, gw = 2 * h + 1, 2 * w + 1
    wall = _wall_grid(maze, w, h)
    hint_set = set(hint or ())
    lines: list[str] = []
    for gr in range(gh):
        chars: list[str] = []
        for gc in range(gw):
            if gr % 2 == 1 and gc % 2 == 1:        # cell interior
                cell = ((gc - 1) // 2, (gr - 1) // 2)
                if cell == player:
                    chars.append(f"{_C_PLAYER}@{_RESET}")
                elif cell == end:
                    chars.append(f"{_C_EXIT}★{_RESET}")   # ★
                elif cell in hint_set:
                    chars.append(f"{_C_HINT}·{_RESET}")    # ·
                else:
                    chars.append(" ")
            elif gr % 2 == 0 and gc % 2 == 0:      # wall junction
                arms = (
                    gr > 0 and wall[gr - 1][gc],          # N
                    gr + 1 < gh and wall[gr + 1][gc],     # S
                    gc + 1 < gw and wall[gr][gc + 1],     # E
                    gc > 0 and wall[gr][gc - 1],          # W
                )
                glyph = _BOX[arms]
                chars.append(f"{_C_WALL}{glyph}{_RESET}" if glyph != " " else " ")
            elif gr % 2 == 0:                      # horizontal edge
                chars.append(f"{_C_WALL}─{_RESET}" if wall[gr][gc] else " ")
            else:                                  # vertical edge
                chars.append(f"{_C_WALL}│{_RESET}" if wall[gr][gc] else " ")
        lines.append("  " + "".join(chars))
    return lines


def _frame(
    maze: dict,
    w: int,
    h: int,
    player: tuple[int, int],
    end: tuple[int, int],
    *,
    moves: int,
    elapsed: float,
    hint: list[tuple[int, int]] | None,
    won: bool,
) -> str:
    out = [
        f"  {_C_ACCENT}\U0001f300 Maze{_RESET}   "
        f"{_C_DIM}moves {moves} · {elapsed:.0f}s{_RESET}",
        "",
    ]
    out.extend(_maze_lines(maze, w, h, player, end, hint))
    out.append("")
    if won:
        out.append(
            f"  {_C_EXIT}\U0001f389 you escaped!{_RESET} "
            f"{_C_DIM}{moves} moves · {elapsed:.0f}s — press any key{_RESET}"
        )
    else:
        out.append(
            f"  {_C_DIM}←↑↓→ move · "
            f"h hint · q quit{_RESET}"
        )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Interactive driver
# ---------------------------------------------------------------------------
def _fit_size() -> tuple[int, int]:
    """Pick a maze (w, h) that fits the current terminal, with sane caps."""
    cols, rows = shutil.get_terminal_size((80, 24))
    w = max(6, min(30, (cols - 4) // 2))
    h = max(5, min(16, (rows - 8) // 2))
    return w, h


def _play(console: Console) -> None:
    header(console, GAME)

    if not rt.is_interactive():
        console.print(
            f"  [{WARN}]Maze needs a real terminal — run it directly "
            f"in your shell \U0001f300[/{WARN}]\n"
        )
        return

    w, h = _fit_size()
    maze = generate_maze(w, h, random.Random())
    start = (0, 0)
    end = (w - 1, h - 1)
    player = start
    moves = 0
    elapsed = 0.0
    start_time = time.time()
    hint_path: list[tuple[int, int]] | None = None
    hint_until = 0.0
    won = False
    quit_now = False

    rt.enter_fullscreen()
    try:
        with rt.raw_mode():
            while True:
                now = time.time()
                elapsed = now - start_time
                active_hint = hint_path if now < hint_until else None
                rt.draw(
                    _frame(
                        maze, w, h, player, end,
                        moves=moves, elapsed=elapsed,
                        hint=active_hint, won=won,
                    )
                )
                if won:
                    rt.read_key()   # any key dismisses the win screen
                    break

                key = rt.poll_key(0.12)   # poll so the clock/hint stay live
                if key in ("q", "esc"):
                    quit_now = True
                    break
                if key == "h":
                    hint_path = solve(maze, player, end, w, h)
                    hint_until = time.time() + 1.6
                elif key in _DIRS:
                    dx, dy = _DIRS[key]
                    target = (player[0] + dx, player[1] + dy)
                    if target in maze.get(player, ()):   # open passage only
                        player = target
                        moves += 1
                        if player == end:
                            won = True
                            elapsed = time.time() - start_time
    except Exception:  # noqa: BLE001 — never crash the terminal session
        pass
    finally:
        rt.exit_fullscreen()

    if quit_now:
        console.print(f"  [{MUTE}]left the maze — {moves} moves[/{MUTE}]\n")
    else:
        console.print(
            f"  [bold {GOOD}]escaped![/bold {GOOD}] "
            f"[{MUTE}]{moves} moves · {elapsed:.0f}s[/{MUTE}]\n"
        )


GAME = GameMeta(
    key="maze",
    name="Maze",
    emoji="\U0001f300",
    desc="escape the maze — arrow keys to the exit",
    play=_play,
)
