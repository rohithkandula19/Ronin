"""Memory Match (concentration) — flip cards on a 4x4 grid and find all 8 pairs.

Positions are numbered 1-16. Each turn you name two positions to flip; matching
cards stay revealed, others flip back. Win when every pair is found."""
from __future__ import annotations

import random
import time

from rich.console import Console

from ._engine import GameMeta, ask_line, header

EMOJIS = ["🐼", "🚀", "🍕", "🎲", "🌵", "🎸", "🐙", "🍩",
          "⚡", "🦊", "🌙", "🍓", "🎯", "🐝", "🍔", "🦖"]


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


def _render(console, board, matched, reveal=()):
    """Show the grid; matched/revealed positions show their emoji, else a number."""
    show = set(matched) | set(reveal)
    cells = []
    for idx in range(len(board)):
        if idx in show:
            cells.append(f"  {board[idx]} ")
        else:
            cells.append(f"[dim]{idx + 1:>3}[/dim] ")
    console.print()
    for row in range(4):
        console.print("  " + "".join(cells[row * 4:row * 4 + 4]))
    console.print()


def _parse_positions(raw, size):
    """Return (i, j) 0-based from a string like '1 2', or None if not two ints."""
    parts = raw.replace(",", " ").split()
    nums = []
    for part in parts:
        if part.isdigit():
            nums.append(int(part))
    if len(nums) != 2:
        return None
    i, j = nums[0] - 1, nums[1] - 1
    if not (0 <= i < size and 0 <= j < size):
        return None
    return i, j


def _ask_one(console, prompt, size, matched):
    """Ask for a single position; returns a 0-based index, or None to quit."""
    while True:
        raw = ask_line(console, prompt)
        if raw.lower() in ("q", "quit", ""):
            return None
        if not raw.isdigit():
            console.print("  [yellow]enter a number 1-16 (or q to quit)[/yellow]")
            continue
        pos = int(raw) - 1
        if not (0 <= pos < size):
            console.print("  [yellow]pick a position 1-16[/yellow]")
            continue
        if pos in matched:
            console.print("  [yellow]that pair is already found — pick another[/yellow]")
            continue
        return pos


def _play(console: Console) -> None:
    header(console, GAME)
    board = make_board(EMOJIS[:8], random.Random())
    size = len(board)
    matched: set[int] = set()
    moves = 0
    console.print("  [dim]Name two positions to flip. Type '1 2' or one at a "
                  "time. (q to quit)[/dim]")
    _render(console, board, matched)

    while len(matched) < size:
        first = second = None
        raw = ask_line(console, "first & second (e.g. 1 2):")
        if raw.lower() in ("q", "quit", ""):
            console.print("\n  [dim]gg![/dim]")
            return

        parsed = _parse_positions(raw, size)
        if parsed is not None:
            first, second = parsed
            if first in matched or second in matched:
                console.print("  [yellow]one of those is already found — try "
                              "again[/yellow]")
                continue
            if first == second:
                console.print("  [yellow]pick two different positions[/yellow]")
                continue
        else:
            # Fall back to asking one at a time.
            if raw.isdigit() and 1 <= int(raw) <= size and (int(raw) - 1) not in matched:
                first = int(raw) - 1
            else:
                first = _ask_one(console, "first position:", size, matched)
                if first is None:
                    console.print("\n  [dim]gg![/dim]")
                    return
            second = _ask_one(console, "second position:", size, matched)
            if second is None:
                console.print("\n  [dim]gg![/dim]")
                return
            if second == first:
                console.print("  [yellow]pick two different positions[/yellow]")
                continue

        moves += 1
        # Briefly reveal both flipped cards.
        _render(console, board, matched, reveal=(first, second))
        if is_match(board, first, second):
            matched.add(first)
            matched.add(second)
            console.print(f"  [bold #9ece6a]✓ match![/bold #9ece6a] "
                          f"[dim]({len(matched) // 2}/{size // 2} pairs)[/dim]")
        else:
            console.print("  [#f7768e]✗ no match — flipping back…[/#f7768e]")
            try:
                time.sleep(1.1)
            except Exception:
                pass
            _render(console, board, matched)

    console.print(f"\n  [bold #9ece6a]🎉 All pairs found in {moves} moves![/bold "
                  f"#9ece6a]")


GAME = GameMeta(key="memory", name="Memory Match", emoji="🧠",
                desc="flip cards, find the pairs", play=_play)
