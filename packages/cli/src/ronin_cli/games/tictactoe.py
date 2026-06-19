"""Tic-Tac-Toe vs ronin — the panda plays a perfect minimax game (you won't win,
but you can force a draw)."""
from __future__ import annotations

from rich.console import Console

from ._engine import GameMeta, ask_line, header

WINS = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6),
        (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]


def winner(board: list[str]) -> str | None:
    """Pure rule: ``'X'``/``'O'`` if a line is taken, ``'draw'`` if full, else None."""
    for a, b, c in WINS:
        if board[a] != " " and board[a] == board[b] == board[c]:
            return board[a]
    return "draw" if " " not in board else None


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


def _render(console: Console, board: list[str]) -> None:
    cells = [c if c != " " else str(i + 1) for i, c in enumerate(board)]
    rows = [" {} │ {} │ {} ".format(*cells[r:r + 3]) for r in (0, 3, 6)]
    console.print("  " + "\n  ───┼───┼───\n  ".join(rows) + "\n")


def _play(console: Console) -> None:
    header(console, GAME)
    board = [" "] * 9
    you, panda = "X", "O"
    console.print("  [dim]You're X. Type a cell number 1-9. (q to quit)[/dim]\n")
    _render(console, board)
    while True:
        raw = ask_line(console, "your move:")
        if raw.lower() in ("q", "quit", ""):
            console.print("\n  [dim]gg![/dim]")
            return
        if not (raw.isdigit() and 1 <= int(raw) <= 9 and board[int(raw) - 1] == " "):
            console.print("  [yellow]pick an empty cell 1-9[/yellow]")
            continue
        board[int(raw) - 1] = you
        if winner(board) is None:
            board[best_move(board, panda)] = panda
        _render(console, board)
        w = winner(board)
        if w == you:
            console.print("  [bold #9ece6a]🎉 You beat the panda![/bold #9ece6a]")
            return
        if w == panda:
            console.print("  [bold #f7768e]🐼 ronin wins.[/bold #f7768e]")
            return
        if w == "draw":
            console.print("  [bold #e0af68]🤝 Draw — well played.[/bold #e0af68]")
            return


GAME = GameMeta(key="ttt", name="Tic-Tac-Toe", emoji="⭕",
                desc="vs a perfect minimax panda", play=_play)
