"""Memory Match (concentration) — a real 4x4 card-flip game.

A full-screen 4x4 grid of face-down cards. Move a CURSOR with the ARROW KEYS,
press space/enter to flip the card under it. Flip two: matching cards stay
revealed (highlighted green); a mismatch shows briefly, then both flip back.
Track moves and time; win when all 8 pairs are found, then see a final score.

The *rules* live in pure, I/O-free functions (``make_board``, ``is_match``) so
they can be unit-tested without a terminal. ``_play`` drives a turn-based
raw-mode loop via the shared real-time engine. If a real terminal isn't
available (headless / piped stdin) it falls back to a typed "flip two
positions" prompt and never enters raw mode.
"""
from __future__ import annotations

import random
import time

from rich.console import Console

from ._engine import GameMeta, ask_line, header

EMOJIS = ["🐼", "🚀", "🍕", "🎲", "🌵", "🎸", "🐙", "🍩",
          "⚡", "🦊", "🌙", "🍓", "🎯", "🐝", "🍔", "🦖"]

GRID = 4               # 4x4 board
SIZE = GRID * GRID     # 16 cards / 8 pairs

# Raw ANSI truecolor (Rich markup does NOT render in raw/fullscreen mode).
_TEAL = "\x1b[38;2;45;212;191m"
_GREEN = "\x1b[38;2;158;206;106m"
_AMBER = "\x1b[38;2;224;175;104m"
_MUTE = "\x1b[38;2;107;112;137m"
_RESET = "\x1b[0m"
_BG_TEAL = "\x1b[48;2;45;212;191m\x1b[38;2;15;20;30m"  # teal bg, dark fg
_BOLD = "\x1b[1m"


# ---------------------------------------------------------------------------
# Pure rules (no I/O)
# ---------------------------------------------------------------------------
def make_board(pairs, rng):
    """Pure rule: shuffled list of two of each value in ``pairs``.

    Uses the passed ``random.Random`` so the layout is deterministic for tests.
    """
    cards = [value for value in pairs for _ in range(2)]
    rng.shuffle(cards)
    return cards


def is_match(board, i, j):
    """Pure rule: True if positions ``i`` and ``j`` (0-based) hold the same card."""
    if i == j:
        return False
    if not (0 <= i < len(board) and 0 <= j < len(board)):
        return False
    return board[i] == board[j]


# ---------------------------------------------------------------------------
# Rendering (raw ANSI — used inside fullscreen/raw mode)
# ---------------------------------------------------------------------------
def _fmt_time(seconds: float) -> str:
    secs = int(seconds)
    return f"{secs // 60:d}:{secs % 60:02d}"


def _cell_text(board, idx, *, matched, reveal):
    """The 3-char glyph shown in a cell, plus a colour tag for it."""
    if idx in matched:
        return board[idx], "matched"
    if idx in reveal:
        return board[idx], "reveal"
    return "▩", "down"


def _frame(board, cursor, matched, reveal, moves, start_ts, *,
           message="", won=False):
    """Build the full-screen frame as a single ANSI string."""
    elapsed = (time.monotonic() - start_ts) if start_ts else 0.0
    pairs_found = len(matched) // 2

    lines = []
    lines.append(f"  {_BOLD}{_TEAL}🧠  Memory Match{_RESET}"
                 f"   {_MUTE}find all 8 pairs{_RESET}")
    lines.append("")
    lines.append(
        f"  {_MUTE}pairs{_RESET} {_GREEN}{pairs_found}/8{_RESET}"
        f"   {_MUTE}moves{_RESET} {_AMBER}{moves}{_RESET}"
        f"   {_MUTE}time{_RESET} {_AMBER}{_fmt_time(elapsed)}{_RESET}"
    )
    lines.append("")

    # Each card occupies a 7-wide cell so emoji (width 2) sit centered.
    cell_w = 7
    top = "  " + _MUTE + "┌" + ("─" * cell_w + "┬") * (GRID - 1) + "─" * cell_w + "┐" + _RESET
    mid = "  " + _MUTE + "├" + ("─" * cell_w + "┼") * (GRID - 1) + "─" * cell_w + "┤" + _RESET
    bot = "  " + _MUTE + "└" + ("─" * cell_w + "┴") * (GRID - 1) + "─" * cell_w + "┘" + _RESET

    for row in range(GRID):
        lines.append(top if row == 0 else mid)
        spacer = []   # one blank padding line above glyph
        glyphs = []
        for col in range(GRID):
            idx = row * GRID + col
            glyph, kind = _cell_text(board, idx, matched=matched, reveal=reveal)
            is_cursor = (idx == cursor) and not won

            if kind == "matched":
                color = _GREEN
            elif kind == "reveal":
                color = _AMBER
            else:
                color = _TEAL

            # glyph width: emoji render as 2 cols, the ▩ as 1.
            glyph_w = 2 if glyph != "▩" else 1
            pad = cell_w - glyph_w
            left = pad // 2
            right = pad - left
            body = " " * left + glyph + " " * right

            if is_cursor:
                blank = f"{_BG_TEAL}{' ' * cell_w}{_RESET}"
                painted = f"{_BG_TEAL}{_BOLD}{body}{_RESET}"
            else:
                blank = " " * cell_w
                painted = f"{color}{_BOLD if kind != 'down' else ''}{body}{_RESET}"

            sep = _MUTE + "│" + _RESET
            if col == 0:
                spacer.append("  " + sep + blank)
                glyphs.append("  " + sep + painted)
            else:
                spacer.append(blank)
                glyphs.append(painted)
        end = _MUTE + "│" + _RESET
        lines.append("".join(spacer) + end)
        lines.append("".join(glyphs) + end)
        lines.append("".join(spacer) + end)
    lines.append(bot)
    lines.append("")

    if won:
        lines.append(f"  {_BOLD}{_GREEN}🎉 All 8 pairs found!{_RESET}")
        lines.append(f"  {_MUTE}{moves} moves · {_fmt_time(elapsed)} · "
                     f"score {_score(moves, elapsed)}{_RESET}")
        lines.append("")
        lines.append(f"  {_MUTE}press any key to finish{_RESET}")
    elif message:
        lines.append(f"  {message}")
    else:
        lines.append(f"  {_MUTE}arrows move · space/enter flips · q quits{_RESET}")

    return "\n".join(lines)


def _score(moves: int, elapsed: float) -> int:
    """Higher is better. Perfect play is 8 pairs in 8 moves."""
    base = 1000
    penalty = max(0, moves - 8) * 20 + int(elapsed) * 2
    return max(50, base - penalty)


# ---------------------------------------------------------------------------
# Non-interactive fallback (typed prompt; never raw mode)
# ---------------------------------------------------------------------------
def _parse_positions(raw, size):
    """Return (i, j) 0-based from a string like '1 2', or None."""
    parts = raw.replace(",", " ").split()
    nums = [int(p) for p in parts if p.isdigit()]
    if len(nums) != 2:
        return None
    i, j = nums[0] - 1, nums[1] - 1
    if not (0 <= i < size and 0 <= j < size) or i == j:
        return None
    return i, j


def _render_typed(console, board, matched, reveal=()):
    show = set(matched) | set(reveal)
    cells = []
    for idx in range(SIZE):
        if idx in show:
            cells.append(f"  {board[idx]} ")
        else:
            cells.append(f"[dim]{idx + 1:>3}[/dim] ")
    console.print()
    for row in range(GRID):
        console.print("  " + "".join(cells[row * GRID:row * GRID + GRID]))
    console.print()


def _play_typed(console: Console) -> None:
    board = make_board(EMOJIS[:8], random.Random())
    matched: set[int] = set()
    moves = 0
    start = time.monotonic()
    console.print("  [dim]Flip two positions 1-16, e.g. '1 2'. (q to quit)[/dim]")
    _render_typed(console, board, matched)

    while len(matched) < SIZE:
        raw = ask_line(console, "flip two (e.g. 1 2):")
        if raw.lower() in ("q", "quit", ""):
            console.print("\n  [dim]gg![/dim]")
            return
        parsed = _parse_positions(raw, SIZE)
        if parsed is None:
            console.print("  [yellow]enter two different numbers 1-16[/yellow]")
            continue
        i, j = parsed
        if i in matched or j in matched:
            console.print("  [yellow]one of those is already found[/yellow]")
            continue
        moves += 1
        _render_typed(console, board, matched, reveal=(i, j))
        if is_match(board, i, j):
            matched.update((i, j))
            console.print(f"  [bold #9ece6a]✓ match![/bold #9ece6a] "
                          f"[dim]({len(matched) // 2}/8 pairs)[/dim]")
        else:
            console.print("  [#f7768e]✗ no match — flipping back…[/#f7768e]")
            try:
                time.sleep(0.9)
            except Exception:
                pass
            _render_typed(console, board, matched)

    elapsed = time.monotonic() - start
    console.print(f"\n  [bold #9ece6a]🎉 All 8 pairs in {moves} moves · "
                  f"{_fmt_time(elapsed)} · score {_score(moves, elapsed)}![/bold "
                  f"#9ece6a]\n")


# ---------------------------------------------------------------------------
# Interactive full-screen game
# ---------------------------------------------------------------------------
def _play(console: Console) -> None:
    header(console, GAME)
    from . import _realtime as rt

    if not rt.is_interactive():
        _play_typed(console)
        return

    board = make_board(EMOJIS[:8], random.Random())
    matched: set[int] = set()
    cursor = 0
    selected: list[int] = []   # 0, 1, or (briefly) 2 flipped-but-unmatched
    moves = 0
    start = time.monotonic()
    message = ""
    quit_now = False

    rt.enter_fullscreen()
    try:
        with rt.raw_mode():
            while True:
                reveal = set(selected)
                won = len(matched) == SIZE
                rt.draw(_frame(board, cursor, matched, reveal, moves, start,
                               message=message, won=won))
                if won:
                    rt.read_key()
                    break

                key = rt.read_key()
                message = ""

                if key in ("q", "esc"):
                    quit_now = True
                    break

                if key == "up" and cursor >= GRID:
                    cursor -= GRID
                elif key == "down" and cursor < SIZE - GRID:
                    cursor += GRID
                elif key == "left" and cursor % GRID != 0:
                    cursor -= 1
                elif key == "right" and cursor % GRID != GRID - 1:
                    cursor += 1
                elif key in ("space", "enter"):
                    if cursor in matched or cursor in selected:
                        message = f"{_MUTE}already showing — pick another{_RESET}"
                        continue
                    selected.append(cursor)
                    if len(selected) == 2:
                        first, second = selected
                        moves += 1
                        if is_match(board, first, second):
                            matched.update((first, second))
                            selected = []
                            message = f"{_GREEN}✓ match!{_RESET}"
                        else:
                            # Show both, pause, then flip back.
                            rt.draw(_frame(board, cursor, matched,
                                           set(selected), moves, start,
                                           message=f"{_AMBER}✗ no match…{_RESET}"))
                            try:
                                time.sleep(0.9)
                            except Exception:
                                pass
                            selected = []
    finally:
        rt.exit_fullscreen()

    elapsed = time.monotonic() - start
    if quit_now:
        console.print(f"  [#6b7089]bye — {len(matched) // 2}/8 pairs in "
                      f"{moves} moves[/#6b7089]\n")
    else:
        console.print(f"  [bold #9ece6a]🎉 All 8 pairs found![/bold #9ece6a] "
                      f"[#6b7089]{moves} moves · {_fmt_time(elapsed)} · "
                      f"score {_score(moves, elapsed)}[/#6b7089]\n")


GAME = GameMeta(key="memory", name="Memory Match", emoji="🧠",
                desc="flip cards, find the pairs", play=_play)
