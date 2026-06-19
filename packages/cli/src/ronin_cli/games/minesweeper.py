"""Minesweeper — a 9x9 field with 10 hidden mines. Reveal cells without hitting a
mine; the first reveal is always safe. Rules live in pure functions so they can be
unit-tested without a terminal."""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

ROWS, COLS, MINES = 9, 9, 10


def neighbors(r: int, c: int, rows: int, cols: int) -> list[tuple[int, int]]:
    """Pure rule: in-bounds cells touching (r, c), excluding the cell itself."""
    out: list[tuple[int, int]] = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                out.append((nr, nc))
    return out


def count_adjacent(mines: set, r: int, c: int, rows: int, cols: int) -> int:
    """Pure rule: how many of (r, c)'s neighbors are mines."""
    return sum(1 for n in neighbors(r, c, rows, cols) if n in mines)


def flood_reveal(mines: set, revealed: set, r: int, c: int, rows: int, cols: int) -> None:
    """Reveal (r, c), flood-filling outward through zero-count cells. Mutates
    ``revealed`` in place. Does not reveal mines."""
    if (r, c) in revealed or (r, c) in mines:
        return
    revealed.add((r, c))
    if count_adjacent(mines, r, c, rows, cols) == 0:
        for nr, nc in neighbors(r, c, rows, cols):
            flood_reveal(mines, revealed, nr, nc, rows, cols)


def _place_mines(rows: int, cols: int, count: int, safe: tuple[int, int]) -> set:
    """Pick ``count`` random mine cells, never landing on ``safe`` or its neighbors."""
    forbidden = {safe} | set(neighbors(safe[0], safe[1], rows, cols))
    cells = [(r, c) for r in range(rows) for c in range(cols) if (r, c) not in forbidden]
    return set(random.sample(cells, min(count, len(cells))))


def _render(console: Console, mines: set, revealed: set, flags: set,
            rows: int, cols: int, *, show_mines: bool = False) -> None:
    head = "     " + " ".join(str(c) for c in range(cols))
    console.print(f"  [dim]{head}[/dim]")
    for r in range(rows):
        cells = []
        for c in range(cols):
            if show_mines and (r, c) in mines:
                cells.append("[bold #f7768e]*[/bold #f7768e]")
            elif (r, c) in flags:
                cells.append("[bold #e0af68]⚑[/bold #e0af68]")
            elif (r, c) in revealed:
                n = count_adjacent(mines, r, c, rows, cols)
                cells.append("[dim] [/dim]" if n == 0 else f"[#7aa2f7]{n}[/#7aa2f7]")
            else:
                cells.append("[dim]·[/dim]")
        console.print(f"  [dim]{r:>2}[/dim]  " + " ".join(cells))
    console.print()


def _play(console: Console) -> None:
    header(console, GAME)
    mines: set = set()
    revealed: set = set()
    flags: set = set()
    placed = False
    total_safe = ROWS * COLS - MINES
    console.print("  [dim]9x9, 10 mines. Type \"r 3 4\" to reveal or \"f 3 4\" to flag "
                  "(row col). q to quit.[/dim]\n")
    _render(console, mines, revealed, flags, ROWS, COLS)

    while True:
        raw = ask_line(console, "r/f row col:")
        low = raw.lower()
        if low in ("q", "quit", ""):
            console.print("\n  [dim]gg![/dim]")
            return
        parts = low.split()
        if len(parts) != 3 or parts[0] not in ("r", "f"):
            console.print("  [yellow]format: r row col  /  f row col[/yellow]")
            continue
        if not (parts[1].isdigit() and parts[2].isdigit()):
            console.print("  [yellow]row and col must be numbers[/yellow]")
            continue
        cmd, r, c = parts[0], int(parts[1]), int(parts[2])
        if not (0 <= r < ROWS and 0 <= c < COLS):
            console.print(f"  [yellow]row/col must be 0-{ROWS - 1}[/yellow]")
            continue

        if cmd == "f":
            if (r, c) in revealed:
                console.print("  [yellow]can't flag a revealed cell[/yellow]")
                continue
            flags.discard((r, c)) if (r, c) in flags else flags.add((r, c))
            _render(console, mines, revealed, flags, ROWS, COLS)
            continue

        # reveal
        if (r, c) in flags:
            console.print("  [yellow]unflag it first[/yellow]")
            continue
        if (r, c) in revealed:
            console.print("  [yellow]already revealed[/yellow]")
            continue
        if not placed:
            mines = _place_mines(ROWS, COLS, MINES, (r, c))
            placed = True
        if (r, c) in mines:
            revealed.add((r, c))
            _render(console, mines, revealed, flags, ROWS, COLS, show_mines=True)
            console.print("  [bold #f7768e]\U0001f4a5 Boom! You hit a mine.[/bold #f7768e]")
            return
        flood_reveal(mines, revealed, r, c, ROWS, COLS)
        _render(console, mines, revealed, flags, ROWS, COLS)
        if len(revealed) >= total_safe:
            console.print("  [bold #9ece6a]\U0001f3c6 Field cleared — you win![/bold #9ece6a]")
            return


GAME = GameMeta(key="mines", name="Minesweeper", emoji="\U0001f4a3",
                desc="clear the field without hitting a mine", play=_play)
