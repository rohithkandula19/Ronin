"""Minesweeper — a 9x9 field with 10 hidden mines. Move a cursor with the arrow
keys, reveal with space/enter, flag with 'f'; the first reveal is always safe and
zero-count cells flood-fill outward. Rules live in pure functions so they can be
unit-tested without a terminal; the real-time loop drives them via ``_realtime``.
On a non-TTY we fall back to a typed ``r/f row col`` prompt rather than crashing.
"""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

ROWS, COLS, MINES = 9, 9, 10


# ---------------------------------------------------------------------------
# Pure rules (no I/O) — tests depend on these names/signatures.
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Raw ANSI palette (Rich markup does NOT render in raw/fullscreen mode).
# ---------------------------------------------------------------------------
_TEAL = "\x1b[38;2;45;212;191m"
_SKY = "\x1b[38;2;125;211;252m"
_GREEN = "\x1b[38;2;158;206;106m"
_ROSE = "\x1b[38;2;247;118;142m"
_AMBER = "\x1b[38;2;224;175;104m"
_MUTE = "\x1b[38;2;107;112;137m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"
# cursor cell: teal background, black foreground
_CURSOR = "\x1b[48;2;45;212;191m\x1b[38;2;0;0;0m"

# number → colour (classic Minesweeper-ish)
_NUMC = {
    1: "\x1b[38;2;125;211;252m",   # blue / sky
    2: "\x1b[38;2;158;206;106m",   # green
    3: "\x1b[38;2;247;118;142m",   # red / rose
    4: "\x1b[38;2;187;154;247m",   # purple
    5: "\x1b[38;2;224;175;104m",   # amber
    6: "\x1b[38;2;45;212;191m",    # teal
    7: "\x1b[38;2;192;202;245m",   # light
    8: "\x1b[38;2;107;112;137m",   # mute
}


def _cell_glyph(mines: set, revealed: set, flags: set, r: int, c: int,
                rows: int, cols: int, *, show_mines: bool, exploded) -> tuple[str, str]:
    """Return (colour_prefix, single-char glyph) for cell (r, c)."""
    pos = (r, c)
    if show_mines and pos in mines:
        if pos == exploded:
            return _ROSE + _BOLD, "✸"
        if pos in flags:
            return _GREEN, "⚑"
        return _MUTE, "✸"
    if pos in flags:
        return _AMBER + _BOLD, "⚑"
    if pos in revealed:
        n = count_adjacent(mines, r, c, rows, cols)
        if n == 0:
            return _MUTE, " "
        return _NUMC.get(n, _SKY) + _BOLD, str(n)
    return _MUTE, "·"


def _frame(mines: set, revealed: set, flags: set, cur: tuple[int, int],
           rows: int, cols: int, *, status: str, show_mines: bool = False,
           exploded=None, interactive: bool = True) -> str:
    mines_left = MINES - len(flags)
    lines = [
        f"  {_TEAL}{_BOLD}\U0001f4a3 Minesweeper{_RESET}   "
        f"{_ROSE}⚑ {mines_left:>2}{_RESET}   "
        f"{_MUTE}revealed {len(revealed)}/{rows * cols - MINES}{_RESET}",
        "",
    ]
    # column header
    head = "     " + " ".join(f"{c}" for c in range(cols))
    lines.append(f"{_MUTE}{head}{_RESET}")
    lines.append(f"   {_MUTE}┌{'─' * (cols * 2 + 1)}┐{_RESET}")
    for r in range(rows):
        cells = []
        for c in range(cols):
            colour, glyph = _cell_glyph(mines, revealed, flags, r, c, rows, cols,
                                        show_mines=show_mines, exploded=exploded)
            if (r, c) == cur and not show_mines:
                cells.append(f"{_CURSOR}{glyph}{_RESET}")
            else:
                cells.append(f"{colour}{glyph}{_RESET}")
        row = " ".join(cells)
        lines.append(f" {_MUTE}{r:>1}│{_RESET} {row} {_MUTE}│{_RESET}")
    lines.append(f"   {_MUTE}└{'─' * (cols * 2 + 1)}┘{_RESET}")
    lines.append("")
    if status:
        lines.append("  " + status)
    else:
        lines.append(f"  {_MUTE}arrows move · space/enter reveal · "
                     f"f flag · q quit{_RESET}")
    return "\n".join(lines)


def _play(console: Console) -> None:
    header(console, GAME)
    from . import _realtime as rt

    if not rt.is_interactive():
        _play_fallback(console)
        return

    mines: set = set()
    revealed: set = set()
    flags: set = set()
    placed = False
    total_safe = ROWS * COLS - MINES
    cur = (ROWS // 2, COLS // 2)
    over = False          # True once won or lost
    won = False
    exploded = None
    quit_now = False

    rt.enter_fullscreen()
    try:
        with rt.raw_mode():
            while True:
                if over:
                    status = (f"{_GREEN}{_BOLD}\U0001f3c6 Field cleared — you win!{_RESET}"
                              f"  {_MUTE}press any key{_RESET}") if won else (
                              f"{_ROSE}{_BOLD}\U0001f4a5 Boom! You hit a mine.{_RESET}"
                              f"  {_MUTE}press any key{_RESET}")
                    rt.draw(_frame(mines, revealed, flags, cur, ROWS, COLS,
                                   status=status, show_mines=True, exploded=exploded))
                    rt.read_key()
                    break

                rt.draw(_frame(mines, revealed, flags, cur, ROWS, COLS, status=""))
                key = rt.read_key()
                if key in ("q", "esc"):
                    quit_now = True
                    break
                if key == "up":
                    cur = (max(0, cur[0] - 1), cur[1])
                elif key == "down":
                    cur = (min(ROWS - 1, cur[0] + 1), cur[1])
                elif key == "left":
                    cur = (cur[0], max(0, cur[1] - 1))
                elif key == "right":
                    cur = (cur[0], min(COLS - 1, cur[1] + 1))
                elif key == "f":
                    if cur not in revealed:
                        if cur in flags:
                            flags.discard(cur)
                        else:
                            flags.add(cur)
                elif key in ("space", "enter"):
                    if cur in flags or cur in revealed:
                        continue
                    if not placed:
                        mines = _place_mines(ROWS, COLS, MINES, cur)
                        placed = True
                    if cur in mines:
                        revealed.add(cur)
                        exploded = cur
                        over = True
                        won = False
                        continue
                    flood_reveal(mines, revealed, cur[0], cur[1], ROWS, COLS)
                    if len(revealed) >= total_safe:
                        over = True
                        won = True
    finally:
        rt.exit_fullscreen()

    if quit_now:
        console.print(f"  [#6b7089]bye — revealed {len(revealed)}/{total_safe}[/#6b7089]\n")
    elif won:
        console.print("  [bold #9ece6a]\U0001f3c6 Field cleared — you win![/bold #9ece6a]\n")
    else:
        console.print("  [bold #f7768e]\U0001f4a5 Boom! You hit a mine.[/bold #f7768e]\n")


def _render_plain(console: Console, mines: set, revealed: set, flags: set,
                  *, show_mines: bool = False) -> None:
    """Rich-markup render for the non-interactive typed fallback."""
    head = "     " + " ".join(str(c) for c in range(COLS))
    console.print(f"  [dim]{head}[/dim]")
    for r in range(ROWS):
        cells = []
        for c in range(COLS):
            if show_mines and (r, c) in mines:
                cells.append("[bold #f7768e]✸[/bold #f7768e]")
            elif (r, c) in flags:
                cells.append("[bold #e0af68]⚑[/bold #e0af68]")
            elif (r, c) in revealed:
                n = count_adjacent(mines, r, c, ROWS, COLS)
                if n == 0:
                    cells.append("[dim] [/dim]")
                else:
                    cells.append(f"[#7aa2f7]{n}[/#7aa2f7]")
            else:
                cells.append("[dim]·[/dim]")
        console.print(f"  [dim]{r:>2}[/dim]  " + " ".join(cells))
    console.print()


def _play_fallback(console: Console) -> None:
    """Typed prompt for non-TTY environments (tests / piped stdin)."""
    mines: set = set()
    revealed: set = set()
    flags: set = set()
    placed = False
    total_safe = ROWS * COLS - MINES
    console.print("  [dim]9x9, 10 mines. Type \"r 3 4\" to reveal or \"f 3 4\" to flag "
                  "(row col). q to quit.[/dim]\n")
    _render_plain(console, mines, revealed, flags)

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
            _render_plain(console, mines, revealed, flags)
            continue

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
            _render_plain(console, mines, revealed, flags, show_mines=True)
            console.print("  [bold #f7768e]\U0001f4a5 Boom! You hit a mine.[/bold #f7768e]")
            return
        flood_reveal(mines, revealed, r, c, ROWS, COLS)
        _render_plain(console, mines, revealed, flags)
        if len(revealed) >= total_safe:
            console.print("  [bold #9ece6a]\U0001f3c6 Field cleared — you win![/bold #9ece6a]")
            return


GAME = GameMeta(key="mines", name="Minesweeper", emoji="\U0001f4a3",
                desc="clear the field without hitting a mine", play=_play)
