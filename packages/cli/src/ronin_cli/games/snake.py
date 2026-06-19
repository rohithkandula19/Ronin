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


def _play(console: Console) -> None:
    header(console, GAME)
    console.print(f"  [{MUTE}]arrow keys to steer   q to quit[/{MUTE}]")
    console.print()

    # Mutable game state shared with the prompt_toolkit callbacks.
    start = (WIDTH // 2, HEIGHT // 2)
    state: dict[str, object] = {
        "snake": [start, (start[0] - 1, start[1]), (start[0] - 2, start[1])],
        "direction": "right",
        "pending": "right",
        "food": None,
        "score": 0,
        "over": False,
        "quit": False,
    }
    state["food"] = _spawn_food(state["snake"])  # type: ignore[arg-type]

    def step() -> None:
        if state["over"] or state["quit"]:
            return
        direction = state["pending"]
        state["direction"] = direction
        snake: list[tuple[int, int]] = state["snake"]  # type: ignore[assignment]
        head = next_head(snake[0], direction)  # type: ignore[arg-type]

        # The tail cell about to vacate isn't a collision unless we grow.
        will_eat = head == state["food"]
        body = snake if will_eat else snake[:-1]
        if collides(head, body, WIDTH, HEIGHT):
            state["over"] = True
            return

        snake.insert(0, head)
        if will_eat:
            state["score"] = int(state["score"]) + 1  # type: ignore[arg-type]
            state["food"] = _spawn_food(snake)
        else:
            snake.pop()

    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import Window
        from prompt_toolkit.layout.controls import FormattedTextControl

        def render() -> str:
            snake: list[tuple[int, int]] = state["snake"]  # type: ignore[assignment]
            body = set(snake[1:])
            head = snake[0]
            food = state["food"]
            lines = ["  Snake  —  score %d" % int(state["score"]), ""]  # type: ignore[arg-type]
            top = "  +" + "-" * WIDTH + "+"
            lines.append(top)
            for y in range(HEIGHT):
                row = ["  |"]
                for x in range(WIDTH):
                    cell = (x, y)
                    if cell == head:
                        row.append("@")
                    elif cell in body:
                        row.append("o")
                    elif cell == food:
                        row.append("*")
                    else:
                        row.append(" ")
                row.append("|")
                lines.append("".join(row))
            lines.append(top)
            lines.append("")
            if state["over"]:
                lines.append("  game over — press q")
            else:
                lines.append("  arrows steer · q quits")
            return "\n".join(lines)

        kb = KeyBindings()

        def _steer(new_dir: str) -> None:
            # Ignore reversals into yourself.
            if new_dir != _OPPOSITE.get(str(state["direction"])):
                state["pending"] = new_dir

        @kb.add("up")
        def _(event) -> None:
            _steer("up")

        @kb.add("down")
        def _(event) -> None:
            _steer("down")

        @kb.add("left")
        def _(event) -> None:
            _steer("left")

        @kb.add("right")
        def _(event) -> None:
            _steer("right")

        @kb.add("q")
        @kb.add("c-c")
        def _(event) -> None:
            state["quit"] = True
            event.app.exit()

        control = FormattedTextControl(text=render)
        window = Window(content=control, always_hide_cursor=True)
        app: "Application" = Application(
            layout=Layout(window),
            key_bindings=kb,
            full_screen=False,
            refresh_interval=TICK,
        )

        async def ticker() -> None:
            import asyncio

            while True:
                await asyncio.sleep(TICK)
                if state["quit"]:
                    return
                if not state["over"]:
                    step()
                    app.invalidate()

        async def run() -> None:
            task = app.create_background_task(ticker())
            try:
                await app.run_async()
            finally:
                task.cancel()

        import asyncio

        asyncio.run(run())

    except Exception:
        console.print(
            f"  [{WARN}]snake needs a real terminal — "
            f"try running it directly in your shell 🐍[/{WARN}]"
        )
        console.print()
        return

    score = int(state["score"])  # type: ignore[arg-type]
    if state["over"]:
        console.print(
            f"  [bold {ERROR}]game over[/bold {ERROR}] "
            f"[{MUTE}]final score {score}[/{MUTE}]"
        )
    else:
        console.print(f"  [{MUTE}]bye — score {score}[/{MUTE}]")
    console.print()


GAME = GameMeta(
    key="snake",
    name="Snake",
    emoji="🐍",
    desc="eat, grow, don't bite yourself",
    play=_play,
)
