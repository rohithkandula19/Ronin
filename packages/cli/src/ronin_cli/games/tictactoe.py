"""Tic-Tac-Toe vs ronin — the panda plays a perfect minimax game (you won't win,
but you can force a draw).

The *rules* live in pure, I/O-free functions (``winner``, ``best_move``) so they
can be unit-tested without a terminal. ``_play`` drives a turn-based full-screen
loop: a cursor moves over the 3x3 board with the arrow keys and space/enter
places your X. On a non-interactive stream (pipes, tests) it falls back to a
typed "cell 1-9" prompt and never touches raw mode.
"""
from __future__ import annotations

from rich.console import Console

from ._engine import GameMeta, ask_line, header

WINS = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6),
        (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]


# ---------------------------------------------------------------------------
# Pure rules (no I/O) — tests depend on these names/signatures.
# ---------------------------------------------------------------------------
def winner(board: list[str]) -> str | None:
    """Pure rule: ``'X'``/``'O'`` if a line is taken, ``'draw'`` if full, else None."""
    for a, b, c in WINS:
        if board[a] != " " and board[a] == board[b] == board[c]:
            return board[a]
    return "draw" if " " not in board else None


def winning_line(board: list[str]) -> tuple[int, int, int] | None:
    """The three cell indices forming the win, or ``None`` if nobody has won."""
    for a, b, c in WINS:
        if board[a] != " " and board[a] == board[b] == board[c]:
            return (a, b, c)
    return None


def _minimax(board: list[str], me: str, turn: str) -> int:
    """Score the position for ``me`` (+10 win / -10 loss / 0 draw, sooner is better)."""
    w = winner(board)
    if w == me:
        return 10
    if w and w != "draw":
        return -10
    if w == "draw":
        return 0
    other = "O" if turn == "X" else "X"
    scores = []
    for i in range(9):
        if board[i] == " ":
            board[i] = turn
            scores.append(_minimax(board, me, other))
            board[i] = " "
    return max(scores) if turn == me else min(scores)


def best_move(board: list[str], me: str) -> int:
    """Pure rule: index of ronin's optimal move."""
    other = "O" if me == "X" else "X"
    best, choice = -999, -1
    for i in range(9):
        if board[i] == " ":
            board[i] = me
            s = _minimax(board, me, other)
            board[i] = " "
            if s > best:
                best, choice = s, i
    return choice


# ---------------------------------------------------------------------------
# Raw ANSI palette (Rich markup does not render in raw/full-screen mode).
# ---------------------------------------------------------------------------
TEAL = "\x1b[38;2;45;212;191m"
SKY = "\x1b[38;2;125;211;252m"     # X
ROSE = "\x1b[38;2;247;118;142m"    # O
GREEN = "\x1b[38;2;158;206;106m"
AMBER = "\x1b[38;2;224;175;104m"
MUTE = "\x1b[38;2;107;112;137m"
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
TEAL_BG = "\x1b[48;2;45;212;191m\x1b[38;2;15;20;30m"   # cursor highlight
GREEN_BG = "\x1b[48;2;158;206;106m\x1b[38;2;15;20;30m"  # winning cell
ROSE_BG = "\x1b[48;2;247;118;142m\x1b[38;2;15;20;30m"   # losing cell

# Rich palette for the non-raw paths (header / fallback / outro).
_R_GOOD = "#9ece6a"
_R_ERROR = "#f7768e"
_R_WARN = "#e0af68"
_R_MUTE = "#6b7089"

# Big glyphs for X / O — 5 lines tall, drawn into a 5-wide field.
_GLYPH = {
    "X": ["#   #", " # # ", "  #  ", " # # ", "#   #"],
    "O": [" ### ", "#   #", "#   #", "#   #", " ### "],
    " ": ["     ", "     ", "     ", "     ", "     "],
}
_CELL_W = 7   # inner width per cell (1 pad + 5 glyph + 1 pad)
_CELL_H = 5   # glyph height


def _glyph_color(mark: str) -> str:
    return SKY if mark == "X" else ROSE if mark == "O" else MUTE


def _cell_block(idx: int, mark: str, *, cursor: bool, win: bool, lose: bool) -> list[str]:
    """Return the ``_CELL_H`` rendered lines for one board cell."""
    if mark == " " and not cursor:
        # show a faint hint number in the middle line
        hint = [" " * _CELL_W] * _CELL_H
        mid = f"  {MUTE}{idx + 1}{RESET}  "
        hint[_CELL_H // 2] = mid
        return hint

    glyph = _GLYPH[mark]
    color = _glyph_color(mark)
    if win:
        bg = GREEN_BG
    elif lose:
        bg = ROSE_BG
    elif cursor:
        bg = TEAL_BG
    else:
        bg = ""

    lines = []
    for g in glyph:
        inner = " " + g + " "  # pad to _CELL_W
        if mark == " ":
            inner = " " * _CELL_W
        if bg:
            lines.append(f"{bg}{BOLD}{inner}{RESET}")
        else:
            lines.append(f"{color}{BOLD}{inner}{RESET}")
    return lines


def _frame(board: list[str], cursor: int, *, status: str, line: tuple[int, int, int] | None,
           result: str | None) -> str:
    """Build the full ANSI frame for the current board state."""
    win_cells = set(line) if line else set()
    lose = result == "O"
    out: list[str] = []
    out.append(f"  {TEAL}{BOLD}⭕  Tic-Tac-Toe{RESET}   {MUTE}you are "
               f"{SKY}X{MUTE} · panda is {ROSE}O{RESET}")
    out.append("")

    sep_seg = "─" * _CELL_W
    top = f"  {MUTE}┌{sep_seg}┬{sep_seg}┬{sep_seg}┐{RESET}"
    mid_sep = f"  {MUTE}├{sep_seg}┼{sep_seg}┼{sep_seg}┤{RESET}"
    bot = f"  {MUTE}└{sep_seg}┴{sep_seg}┴{sep_seg}┘{RESET}"

    out.append(top)
    for r in range(3):
        # render the three cells of this row, each _CELL_H lines tall
        blocks = []
        for c in range(3):
            i = r * 3 + c
            blocks.append(_cell_block(
                i, board[i],
                cursor=(i == cursor and result is None),
                win=(i in win_cells),
                lose=(lose and i in win_cells),
            ))
        for h in range(_CELL_H):
            row = f"  {MUTE}│{RESET}" + f"{MUTE}│{RESET}".join(blocks[c][h] for c in range(3)) + f"{MUTE}│{RESET}"
            out.append(row)
        if r < 2:
            out.append(mid_sep)
    out.append(bot)
    out.append("")
    out.append("  " + status)
    out.append("")
    if result is None:
        out.append(f"  {MUTE}arrows move · space/enter place · q quits{RESET}")
    else:
        out.append(f"  {MUTE}press any key to leave{RESET}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Non-interactive fallback (pipes / tests): typed cell prompt.
# ---------------------------------------------------------------------------
def _render_plain(console: Console, board: list[str]) -> None:
    cells = [c if c != " " else str(i + 1) for i, c in enumerate(board)]
    rows = [" {} │ {} │ {} ".format(*cells[r:r + 3]) for r in (0, 3, 6)]
    console.print("  " + "\n  ───┼───┼───\n  ".join(rows) + "\n")


def _play_fallback(console: Console) -> None:
    board = [" "] * 9
    you, panda = "X", "O"
    console.print(f"  [{_R_MUTE}]You're X. Type a cell number 1-9. (q to quit)[/{_R_MUTE}]\n")
    _render_plain(console, board)
    while True:
        raw = ask_line(console, "your move:")
        if raw.lower() in ("q", "quit", ""):
            console.print(f"\n  [{_R_MUTE}]gg![/{_R_MUTE}]")
            return
        if not (raw.isdigit() and 1 <= int(raw) <= 9 and board[int(raw) - 1] == " "):
            console.print(f"  [{_R_WARN}]pick an empty cell 1-9[/{_R_WARN}]")
            continue
        board[int(raw) - 1] = you
        if winner(board) is None:
            board[best_move(board, panda)] = panda
        _render_plain(console, board)
        w = winner(board)
        if w == you:
            console.print(f"  [bold {_R_GOOD}]🎉 You beat the panda![/bold {_R_GOOD}]")
            return
        if w == panda:
            console.print(f"  [bold {_R_ERROR}]🐼 ronin wins.[/bold {_R_ERROR}]")
            return
        if w == "draw":
            console.print(f"  [bold {_R_WARN}]🤝 Draw — well played.[/bold {_R_WARN}]")
            return


# ---------------------------------------------------------------------------
# Full-screen interactive loop.
# ---------------------------------------------------------------------------
def _result_status(result: str | None) -> str:
    if result == "X":
        return f"{GREEN}{BOLD}🎉 You beat the panda!{RESET}"
    if result == "O":
        return f"{ROSE}{BOLD}🐼 ronin wins. Try forcing a draw.{RESET}"
    if result == "draw":
        return f"{AMBER}{BOLD}🤝 Draw — perfectly played.{RESET}"
    return f"{MUTE}your move{RESET}"


def _play(console: Console) -> None:
    header(console, GAME)
    from . import _realtime as rt

    if not rt.is_interactive():
        _play_fallback(console)
        return

    board = [" "] * 9
    you, panda = "X", "O"
    cursor = 4  # start in the center
    result: str | None = None
    line: tuple[int, int, int] | None = None
    status = f"{MUTE}your move{RESET}"

    rt.enter_fullscreen()
    try:
        with rt.raw_mode():
            while True:
                rt.draw(_frame(board, cursor, status=status, line=line, result=result))
                if result is not None:
                    rt.read_key()  # any key dismisses the result screen
                    break

                key = rt.read_key()
                if key in ("q", "esc"):
                    break
                if key == "up":
                    if cursor - 3 >= 0:
                        cursor -= 3
                elif key == "down":
                    cursor = cursor + 3 if cursor + 3 < 9 else cursor
                elif key == "left":
                    cursor = cursor - 1 if cursor % 3 != 0 else cursor
                elif key == "right":
                    cursor = cursor + 1 if cursor % 3 != 2 else cursor
                elif key in ("space", "enter"):
                    if board[cursor] != " ":
                        status = f"{AMBER}that cell is taken{RESET}"
                        continue
                    # your move
                    board[cursor] = you
                    result = winner(board)
                    line = winning_line(board)
                    if result is None:
                        # panda replies (unbeatable minimax)
                        board[best_move(board, panda)] = panda
                        result = winner(board)
                        line = winning_line(board)
                    status = _result_status(result) if result else f"{MUTE}your move{RESET}"
                # ignore any other key
    finally:
        rt.exit_fullscreen()

    if result == "X":
        console.print(f"  [bold {_R_GOOD}]🎉 You beat the panda![/bold {_R_GOOD}]\n")
    elif result == "O":
        console.print(f"  [bold {_R_ERROR}]🐼 ronin wins.[/bold {_R_ERROR}]\n")
    elif result == "draw":
        console.print(f"  [bold {_R_WARN}]🤝 Draw — well played.[/bold {_R_WARN}]\n")
    else:
        console.print(f"  [{_R_MUTE}]gg![/{_R_MUTE}]\n")


GAME = GameMeta(key="ttt", name="Tic-Tac-Toe", emoji="⭕",
                desc="vs a perfect minimax panda", play=_play)
