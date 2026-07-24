"""Battleship — sink the panda's fleet on a 10x10 grid.

Standard fleet (carrier 5, battleship 4, cruiser 3, submarine 3, destroyer 2)
is randomly placed for both sides. You fire at the enemy tracking grid by moving
a cursor with the arrow keys (space/enter to fire); the panda fires back with a
HUNT/TARGET AI — random shots until it lands a hit, then it works the adjacent
cells to finish the ship off.

The *rules* live in pure, I/O-free functions (``place_fleet`` and ``all_sunk``)
so they can be unit-tested without a terminal. ``_play`` drives the real-time
loop via ``_realtime``; on a non-TTY we fall back to a typed "row col" prompt
rather than crashing.
"""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header
from . import _realtime as rt

# Fleet definition: (name, length). Order matters for placement & messages.
FLEET = (
    ("carrier", 5),
    ("battleship", 4),
    ("cruiser", 3),
    ("submarine", 3),
    ("destroyer", 2),
)
SIZE = 10


# ---------------------------------------------------------------------------
# Pure rules (no I/O) — tests depend on these names/signatures.
# ---------------------------------------------------------------------------
def place_fleet(rng: random.Random, sizes=(5, 4, 3, 3, 2), size: int = 10) -> list[set]:
    """Randomly place a fleet on a ``size`` x ``size`` grid.

    Returns a list of ships, one per entry in ``sizes``; each ship is a ``set``
    of ``(r, c)`` cells. Ships are straight (horizontal or vertical), fully in
    bounds, and never overlap. Uses the passed :class:`random.Random` for all
    randomness so placements are reproducible in tests.
    """
    ships: list[set] = []
    occupied: set = set()
    for length in sizes:
        ship = _place_one(rng, length, size, occupied)
        ships.append(ship)
        occupied |= ship
    return ships


def _place_one(rng: random.Random, length: int, size: int, occupied: set) -> set:
    """Find a free, in-bounds, straight slot of ``length`` cells avoiding
    ``occupied``. Retries until it fits (always possible for a standard fleet)."""
    while True:
        horizontal = rng.random() < 0.5
        if horizontal:
            r = rng.randrange(size)
            c = rng.randrange(size - length + 1)
            cells = {(r, c + i) for i in range(length)}
        else:
            r = rng.randrange(size - length + 1)
            c = rng.randrange(size)
            cells = {(r + i, c) for i in range(length)}
        if cells & occupied:
            continue
        return cells


def all_sunk(fleet: list[set], hits: set) -> bool:
    """True if every cell of every ship in ``fleet`` is present in ``hits``."""
    for ship in fleet:
        if not ship.issubset(hits):
            return False
    return True


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

_ROWS = "ABCDEFGHIJ"

_MISS = "○"
_HIT = "✸"
_SHIP = "■"
_WATER = "·"


# ---------------------------------------------------------------------------
# Game state helpers
# ---------------------------------------------------------------------------
def _ship_cells(fleet: list[set]) -> set:
    """All cells occupied by any ship in ``fleet``."""
    out: set = set()
    for ship in fleet:
        out |= ship
    return out


def _sunk_now(fleet: list[set], hits: set, just_hit: tuple) -> str | None:
    """If the shot at ``just_hit`` completed (sank) a ship, return that ship's
    name; otherwise None. ``hits`` already includes ``just_hit``."""
    for (name, _length), ship in zip(FLEET, fleet):
        if just_hit in ship and ship.issubset(hits):
            return name
    return None


def _alive_ships(fleet: list[set], hits: set) -> int:
    """How many ships still have at least one un-hit cell."""
    return sum(1 for ship in fleet if not ship.issubset(hits))


# ---------------------------------------------------------------------------
# Panda HUNT/TARGET AI
# ---------------------------------------------------------------------------
def _panda_pick(rng: random.Random, shots: set, targets: list, size: int = SIZE) -> tuple:
    """Choose the panda's next shot.

    If ``targets`` (a queue of promising adjacent cells from a previous hit) has
    any un-fired entries, take one of those (TARGET mode). Otherwise pick a
    random un-fired cell, preferring a parity ("checkerboard") pattern that
    finds ships faster (HUNT mode). Never repeats a shot.
    """
    while targets:
        cell = targets.pop()
        if cell not in shots:
            return cell
    # HUNT: prefer checkerboard cells (every ship has a cell on one parity).
    free = [(r, c) for r in range(size) for c in range(size) if (r, c) not in shots]
    parity = [cell for cell in free if (cell[0] + cell[1]) % 2 == 0]
    pool = parity or free
    return rng.choice(pool)


def _enqueue_targets(targets: list, cell: tuple, shots: set, size: int = SIZE) -> None:
    """Push ``cell``'s orthogonal neighbours onto the TARGET queue (skipping
    out-of-bounds and already-fired cells)."""
    r, c = cell
    for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
        if 0 <= nr < size and 0 <= nc < size and (nr, nc) not in shots:
            targets.append((nr, nc))


# ---------------------------------------------------------------------------
# Rendering (raw ANSI, two grids side by side)
# ---------------------------------------------------------------------------
def _enemy_glyph(cell: tuple, my_shots: set, enemy_cells: set) -> tuple[str, str]:
    """Glyph for a cell on the enemy tracking grid (no un-hit ships shown)."""
    if cell in my_shots:
        if cell in enemy_cells:
            return _ROSE + _BOLD, _HIT
        return _SKY, _MISS
    return _MUTE, _WATER


def _own_glyph(cell: tuple, panda_shots: set, my_cells: set) -> tuple[str, str]:
    """Glyph for a cell on your own fleet grid (ships visible)."""
    on_ship = cell in my_cells
    if cell in panda_shots:
        if on_ship:
            return _ROSE + _BOLD, _HIT
        return _SKY, _MISS
    if on_ship:
        return _TEAL, _SHIP
    return _MUTE, _WATER


def _grid_lines(title: str, glyph, cur: tuple | None) -> list[str]:
    """Build the text lines for one labelled 10x10 grid.

    ``glyph(r, c)`` returns ``(colour, char)``. ``cur`` highlights the cursor
    cell (only used on the enemy grid)."""
    lines = [f"  {_BOLD}{title}{_RESET}"]
    head = "     " + " ".join(f"{c + 1:>2}" for c in range(SIZE))
    lines.append(f"{_MUTE}{head}{_RESET}")
    for r in range(SIZE):
        cells = []
        for c in range(SIZE):
            colour, ch = glyph(r, c)
            if cur is not None and (r, c) == cur:
                cells.append(f"{_CURSOR}{ch}{_RESET}")
            else:
                cells.append(f"{colour}{ch}{_RESET}")
        row = "  ".join(cells)
        lines.append(f"  {_MUTE}{_ROWS[r]}{_RESET}  {row}")
    return lines


def _frame(my_fleet, enemy_fleet, my_shots, panda_shots, cur, *,
           status: str, log: list[str], reveal_enemy: bool = False) -> str:
    """Render the full screen: header, both grids side by side, status, log."""
    my_cells = _ship_cells(my_fleet)
    enemy_cells = _ship_cells(enemy_fleet)
    my_alive = _alive_ships(my_fleet, panda_shots)
    enemy_alive = _alive_ships(enemy_fleet, my_shots)

    def enemy_g(r, c):
        if reveal_enemy and (r, c) in enemy_cells and (r, c) not in my_shots:
            return _MUTE, _SHIP
        return _enemy_glyph((r, c), my_shots, enemy_cells)

    def own_g(r, c):
        return _own_glyph((r, c), panda_shots, my_cells)

    left = _grid_lines(f"{_SKY}your fleet{_RESET}  {_MUTE}({my_alive} afloat){_RESET}",
                       own_g, None)
    right = _grid_lines(f"{_ROSE}enemy waters{_RESET}  {_MUTE}({enemy_alive} left){_RESET}",
                        enemy_g, cur)

    lines = [
        f"  {_TEAL}{_BOLD}🚢 Battleship{_RESET}   "
        f"{_MUTE}sink the panda's fleet{_RESET}",
        "",
    ]
    # join the two grids side by side
    height = max(len(left), len(right))
    gap = "      "
    for i in range(height):
        l = left[i] if i < len(left) else ""
        r = right[i] if i < len(right) else ""
        # pad left column to a fixed visible width (37 visible chars)
        lines.append(_pad_visible(l, 40) + gap + r)
    lines.append("")
    if status:
        lines.append("  " + status)
    else:
        lines.append(f"  {_MUTE}arrows move · space/enter fire · q quit{_RESET}")
    lines.append("")
    for entry in log[-3:]:
        lines.append("  " + entry)
    return "\n".join(lines)


def _pad_visible(s: str, width: int) -> str:
    """Right-pad ``s`` to ``width`` *visible* columns, ignoring ANSI escapes."""
    visible = _visible_len(s)
    if visible >= width:
        return s
    return s + " " * (width - visible)


def _visible_len(s: str) -> int:
    """Length of ``s`` excluding ANSI escape sequences."""
    out = 0
    i = 0
    while i < len(s):
        if s[i] == "\x1b":
            j = s.find("m", i)
            if j == -1:
                break
            i = j + 1
        else:
            out += 1
            i += 1
    return out


# ---------------------------------------------------------------------------
# Interactive play
# ---------------------------------------------------------------------------
def _panda_turn(rng, my_fleet, panda_shots, panda_targets, log) -> bool:
    """Run one panda shot against your fleet. Mutates ``panda_shots``,
    ``panda_targets`` and ``log``. Returns True if your whole fleet is sunk."""
    my_cells = _ship_cells(my_fleet)
    shot = _panda_pick(rng, panda_shots, panda_targets)
    panda_shots.add(shot)
    label = f"{_ROWS[shot[0]]}{shot[1] + 1}"
    if shot in my_cells:
        sunk = _sunk_now(my_fleet, panda_shots, shot)
        if sunk:
            log.append(f"{_ROSE}🐼 panda sank your {sunk}! ({label}){_RESET}")
            # ship's gone — stop chasing its leftover neighbours
            panda_targets.clear()
        else:
            log.append(f"{_AMBER}🐼 panda hits your fleet at {label}{_RESET}")
            _enqueue_targets(panda_targets, shot, panda_shots)
    else:
        log.append(f"{_MUTE}🐼 panda fires at {label} — miss{_RESET}")
    return all_sunk(my_fleet, panda_shots)


def _play(console: Console) -> None:
    header(console, GAME)

    if not rt.is_interactive():
        _play_fallback(console)
        return

    rng = random.Random()
    my_fleet = place_fleet(rng, tuple(n for _, n in FLEET), SIZE)
    enemy_fleet = place_fleet(rng, tuple(n for _, n in FLEET), SIZE)
    my_shots: set = set()
    panda_shots: set = set()
    panda_targets: list = []
    log: list[str] = [f"{_MUTE}fire away — find the panda's ships{_RESET}"]
    cur = (SIZE // 2, SIZE // 2)
    over = False
    won = False
    quit_now = False

    rt.enter_fullscreen()
    try:
        with rt.raw_mode():
            while True:
                if over:
                    if won:
                        status = (f"{_GREEN}{_BOLD}🏆 You sank the panda's whole fleet!"
                                  f"{_RESET}  {_MUTE}press any key{_RESET}")
                    else:
                        status = (f"{_ROSE}{_BOLD}🐼 The panda sank your fleet."
                                  f"{_RESET}  {_MUTE}press any key{_RESET}")
                    rt.draw(_frame(my_fleet, enemy_fleet, my_shots, panda_shots, None,
                                   status=status, log=log, reveal_enemy=True))
                    rt.read_key()
                    break

                rt.draw(_frame(my_fleet, enemy_fleet, my_shots, panda_shots, cur,
                               status="", log=log))
                key = rt.read_key()
                if key in ("q", "esc"):
                    quit_now = True
                    break
                if key == "up":
                    cur = (max(0, cur[0] - 1), cur[1])
                elif key == "down":
                    cur = (min(SIZE - 1, cur[0] + 1), cur[1])
                elif key == "left":
                    cur = (cur[0], max(0, cur[1] - 1))
                elif key == "right":
                    cur = (cur[0], min(SIZE - 1, cur[1] + 1))
                elif key in ("space", "enter"):
                    if cur in my_shots:
                        continue  # already fired here
                    my_shots.add(cur)
                    enemy_cells = _ship_cells(enemy_fleet)
                    label = f"{_ROWS[cur[0]]}{cur[1] + 1}"
                    if cur in enemy_cells:
                        sunk = _sunk_now(enemy_fleet, my_shots, cur)
                        if sunk:
                            log.append(f"{_GREEN}{_BOLD}🎯 sunk the {sunk}!{_RESET}")
                        else:
                            log.append(f"{_TEAL}💥 hit at {label}!{_RESET}")
                        if all_sunk(enemy_fleet, my_shots):
                            over, won = True, True
                            continue
                    else:
                        log.append(f"{_MUTE}splash at {label} — miss{_RESET}")
                    # panda returns fire
                    if _panda_turn(rng, my_fleet, panda_shots, panda_targets, log):
                        over, won = True, False
    finally:
        rt.exit_fullscreen()

    if quit_now:
        console.print(f"  [#6b7089]bye — {_alive_ships(enemy_fleet, my_shots)} "
                      f"enemy ships still afloat[/#6b7089]\n")
    elif won:
        console.print("  [bold #9ece6a]🏆 You sank the panda's whole fleet![/bold #9ece6a]\n")
    else:
        console.print("  [bold #f7768e]🐼 The panda sank your fleet.[/bold #f7768e]\n")


# ---------------------------------------------------------------------------
# Non-TTY fallback (tests / piped stdin) — typed "row col" firing.
# ---------------------------------------------------------------------------
def _render_plain(console: Console, my_fleet, enemy_fleet, my_shots, panda_shots,
                  *, reveal_enemy: bool = False) -> None:
    my_cells = _ship_cells(my_fleet)
    enemy_cells = _ship_cells(enemy_fleet)
    head = "     " + " ".join(f"{c + 1:>2}" for c in range(SIZE))

    def char_enemy(cell):
        if cell in my_shots:
            return "[bold #f7768e]✸[/bold #f7768e]" if cell in enemy_cells \
                else "[#7dd3fc]○[/#7dd3fc]"
        if reveal_enemy and cell in enemy_cells:
            return "[dim]■[/dim]"
        return "[dim]·[/dim]"

    def char_own(cell):
        if cell in panda_shots:
            return "[bold #f7768e]✸[/bold #f7768e]" if cell in my_cells \
                else "[#7dd3fc]○[/#7dd3fc]"
        return "[#2dd4bf]■[/#2dd4bf]" if cell in my_cells else "[dim]·[/dim]"

    console.print(f"  [bold #7dd3fc]your fleet[/bold #7dd3fc]")
    console.print(f"  [dim]{head}[/dim]")
    for r in range(SIZE):
        cells = "  ".join(char_own((r, c)) for c in range(SIZE))
        console.print(f"  [dim]{_ROWS[r]}[/dim]  {cells}")
    console.print(f"\n  [bold #f7768e]enemy waters[/bold #f7768e]")
    console.print(f"  [dim]{head}[/dim]")
    for r in range(SIZE):
        cells = "  ".join(char_enemy((r, c)) for c in range(SIZE))
        console.print(f"  [dim]{_ROWS[r]}[/dim]  {cells}")
    console.print()


def _parse_target(raw: str) -> tuple | None:
    """Parse a typed shot. Accepts "A 5", "a5", "1 5" (row col, 1-based or
    letter row). Returns ``(r, c)`` 0-based, or None if invalid."""
    low = raw.strip().lower().replace(",", " ")
    parts = low.split()
    if len(parts) == 1 and len(low) >= 2:
        # forms like "a5"
        parts = [low[0], low[1:]]
    if len(parts) != 2:
        return None
    rtok, ctok = parts
    if rtok.isalpha() and len(rtok) == 1:
        r = ord(rtok) - ord("a")
    elif rtok.isdigit():
        r = int(rtok) - 1
    else:
        return None
    if not ctok.isdigit():
        return None
    c = int(ctok) - 1
    if 0 <= r < SIZE and 0 <= c < SIZE:
        return (r, c)
    return None


def _play_fallback(console: Console) -> None:
    rng = random.Random()
    my_fleet = place_fleet(rng, tuple(n for _, n in FLEET), SIZE)
    enemy_fleet = place_fleet(rng, tuple(n for _, n in FLEET), SIZE)
    my_shots: set = set()
    panda_shots: set = set()
    panda_targets: list = []
    enemy_cells = _ship_cells(enemy_fleet)

    console.print("  [dim]10x10. Fire by typing a target like \"A 5\" or \"a5\" "
                  "(row letter, column). q to quit.[/dim]\n")
    _render_plain(console, my_fleet, enemy_fleet, my_shots, panda_shots)

    while True:
        raw = ask_line(console, "fire at (row col):")
        if raw.lower() in ("q", "quit", ""):
            console.print("\n  [dim]gg![/dim]")
            return
        target = _parse_target(raw)
        if target is None:
            console.print("  [yellow]format: row col — e.g. A 5 (rows A-J, cols 1-10)[/yellow]")
            continue
        if target in my_shots:
            console.print("  [yellow]already fired there[/yellow]")
            continue
        my_shots.add(target)
        label = f"{_ROWS[target[0]]}{target[1] + 1}"
        if target in enemy_cells:
            sunk = _sunk_now(enemy_fleet, my_shots, target)
            if sunk:
                console.print(f"  [bold #9ece6a]🎯 sunk the {sunk}![/bold #9ece6a]")
            else:
                console.print(f"  [#2dd4bf]💥 hit at {label}![/#2dd4bf]")
            if all_sunk(enemy_fleet, my_shots):
                _render_plain(console, my_fleet, enemy_fleet, my_shots, panda_shots,
                              reveal_enemy=True)
                console.print("  [bold #9ece6a]🏆 You sank the panda's whole fleet!"
                              "[/bold #9ece6a]")
                return
        else:
            console.print(f"  [dim]splash at {label} — miss[/dim]")

        # panda returns fire
        my_cells = _ship_cells(my_fleet)
        shot = _panda_pick(rng, panda_shots, panda_targets)
        panda_shots.add(shot)
        plabel = f"{_ROWS[shot[0]]}{shot[1] + 1}"
        if shot in my_cells:
            psunk = _sunk_now(my_fleet, panda_shots, shot)
            if psunk:
                console.print(f"  [bold #f7768e]🐼 panda sank your {psunk}! "
                              f"({plabel})[/bold #f7768e]")
                panda_targets.clear()
            else:
                console.print(f"  [#e0af68]🐼 panda hits your fleet at {plabel}[/#e0af68]")
                _enqueue_targets(panda_targets, shot, panda_shots)
        else:
            console.print(f"  [dim]🐼 panda fires at {plabel} — miss[/dim]")
        if all_sunk(my_fleet, panda_shots):
            _render_plain(console, my_fleet, enemy_fleet, my_shots, panda_shots,
                          reveal_enemy=True)
            console.print("  [bold #f7768e]🐼 The panda sank your fleet.[/bold #f7768e]")
            return
        _render_plain(console, my_fleet, enemy_fleet, my_shots, panda_shots)


GAME = GameMeta(key="battleship", name="Battleship", emoji="🚢",
                desc="sink the panda's fleet", play=_play)
