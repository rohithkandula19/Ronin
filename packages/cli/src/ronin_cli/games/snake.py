"""Snake — eat food, grow, and don't bite yourself (real-time).

The *rules* live in pure, I/O-free functions (``next_head``, ``collides``) so
they can be unit-tested without a terminal. ``_play`` drives a real-time loop
via prompt_toolkit's ``Application`` with arrow-key steering and a background
timer that advances the snake. If prompt_toolkit can't set up a real terminal
(e.g. headless / piped stdin), we catch it and print a friendly message rather
than crashing.
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

WIDTH = 20
HEIGHT = 12
TICK = 0.12  # seconds between advances

_DELTAS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

_OPPOSITE = {
    "up": "down",
    "down": "up",
    "left": "right",
    "right": "left",
}


# ---------------------------------------------------------------------------
# Pure rules (no I/O)
# ---------------------------------------------------------------------------
def next_head(head: tuple[int, int], direction: str) -> tuple[int, int]:
    """Return the cell the head moves into for ``direction``.

    ``direction`` is one of {up, down, left, right}. Coordinates are ``(x, y)``
    with the origin at the top-left; ``up`` decreases ``y``. An unknown
    direction leaves the head where it is.
    """
    dx, dy = _DELTAS.get(direction, (0, 0))
    return (head[0] + dx, head[1] + dy)


def collides(
    head: tuple[int, int],
    snake_body: list[tuple[int, int]],
    width: int,
    height: int,
) -> bool:
    """True if ``head`` hits a wall or any cell of ``snake_body``.

    A wall hit is any coordinate outside ``[0, width) x [0, height)``. A self
    collision is ``head`` being present in ``snake_body`` (pass the body cells
    the head would overlap with).
    """
    x, y = head
    if x < 0 or x >= width or y < 0 or y >= height:
        return True
    return head in snake_body


# ---------------------------------------------------------------------------
# Interactive helpers
# ---------------------------------------------------------------------------
def _spawn_food(snake: list[tuple[int, int]]) -> tuple[int, int]:
    """Pick a random empty cell for food. Falls back to (0, 0) if full."""
    free = [
        (x, y)
        for x in range(WIDTH)
        for y in range(HEIGHT)
        if (x, y) not in snake
    ]
    return random.choice(free) if free else (0, 0)


# Raw ANSI colours (we bypass Rich while in raw-mode full-screen).
_C_HEAD = "\x1b[1;38;2;158;206;106m"   # bright green
_C_BODY = "\x1b[38;2;110;160;90m"      # dim green
_C_FOOD = "\x1b[38;2;247;118;142m"     # rose
_C_DIM = "\x1b[38;2;107;112;137m"      # mute
_C_ACCENT = "\x1b[1;38;2;45;212;191m"  # teal
_RESET = "\x1b[0m"


def _frame(snake: list[tuple[int, int]], food, score: int, *,
           over: bool, started: bool) -> str:
    body = set(snake[1:])
    head = snake[0]
    lines = [f"  {_C_ACCENT}🐍 Snake{_RESET}   {_C_DIM}score {score}{_RESET}", ""]
    lines.append("  +" + "-" * WIDTH + "+")
    for y in range(HEIGHT):
        row = ["  |"]
        for x in range(WIDTH):
            cell = (x, y)
            if cell == head:
                row.append(f"{_C_HEAD}@{_RESET}")
            elif cell in body:
                row.append(f"{_C_BODY}o{_RESET}")
            elif cell == food:
                row.append(f"{_C_FOOD}*{_RESET}")
            else:
                row.append(" ")
        row.append("|")
        lines.append("".join(row))
    lines.append("  +" + "-" * WIDTH + "+")
    lines.append("")
    if over:
        lines.append(f"  {_C_FOOD}game over{_RESET} {_C_DIM}— press any key{_RESET}")
    elif not started:
        lines.append(f"  {_C_DIM}press an arrow key to start · q quits{_RESET}")
    else:
        lines.append(f"  {_C_DIM}arrows steer · q quits{_RESET}")
    return "\n".join(lines)


def _play(console: Console) -> None:
    header(console, GAME)
    from . import _realtime as rt

    if not rt.is_interactive():
        console.print(f"  [{WARN}]Snake needs a real terminal — run it directly "
                      f"in your shell 🐍[/{WARN}]\n")
        return

    start = (WIDTH // 2, HEIGHT // 2)
    snake = [start, (start[0] - 1, start[1]), (start[0] - 2, start[1])]
    direction = "right"
    food = _spawn_food(snake)
    score = 0
    started = False   # the snake holds still until the first arrow → no cheap deaths
    over = False
    quit_now = False

    rt.enter_fullscreen()
    try:
        with rt.raw_mode():
            while True:
                rt.draw(_frame(snake, food, score, over=over, started=started))
                if over:
                    rt.read_key()   # any key dismisses the game-over screen
                    break
                key = rt.poll_key(TICK)
                if key in ("q", "esc"):
                    quit_now = True
                    break
                if key in _DELTAS:
                    if key != _OPPOSITE.get(direction):
                        direction = key
                    started = True
                if not started:
                    continue
                head = next_head(snake[0], direction)
                will_eat = head == food
                body = snake if will_eat else snake[:-1]
                if collides(head, body, WIDTH, HEIGHT):
                    over = True
                    continue
                snake.insert(0, head)
                if will_eat:
                    score += 1
                    food = _spawn_food(snake)
                else:
                    snake.pop()
    finally:
        rt.exit_fullscreen()

    if quit_now:
        console.print(f"  [{MUTE}]bye — score {score}[/{MUTE}]\n")
    else:
        console.print(f"  [bold {ERROR}]game over[/bold {ERROR}] "
                      f"[{MUTE}]final score {score}[/{MUTE}]\n")


GAME = GameMeta(
    key="snake",
    name="Snake",
    emoji="🐍",
    desc="eat, grow, don't bite yourself",
    play=_play,
)
