"""Reversi (Othello) — flank & flip on an 8x8 board against the panda.

You are Black ('B'), the panda is White ('W'). Standard Othello rules: a legal
move must outflank one or more of the opponent's discs in a straight line
(horizontal, vertical, or diagonal), capturing — and flipping — every disc in
between. When you have no legal move you pass; when neither side can move (or the
board fills) the player with more discs wins.

The board *rules* live in pure, I/O-free functions (``initial_board``,
``legal_moves``, ``apply_move``, ``count``) so they can be unit-tested without a
terminal. ``_play`` drives an arrow-key cursor over a framed grid via the
``_realtime`` engine; on a non-TTY it falls back to a typed "row col" prompt and
never touches raw mode. The panda picks its move with a 1-ply positional search
over a corner/edge weight table.
"""
from __future__ import annotations

from rich.console import Console

from ._engine import GameMeta, ask_line, header

SIZE = 8
EMPTY, BLACK, WHITE = ".", "B", "W"

# Eight ray directions for outflanking checks.
_DIRS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

# Positional value table: corners are gold, the cells next to them are poison,
# edges are solid, the centre is neutral. Used by the panda's 1-ply search.
_WEIGHTS = [
    [120, -20, 20, 5, 5, 20, -20, 120],
    [-20, -40, -5, -5, -5, -5, -40, -20],
    [20, -5, 15, 3, 3, 15, -5, 20],
    [5, -5, 3, 3, 3, 3, -5, 5],
    [5, -5, 3, 3, 3, 3, -5, 5],
    [20, -5, 15, 3, 3, 15, -5, 20],
    [-20, -40, -5, -5, -5, -5, -40, -20],
    [120, -20, 20, 5, 5, 20, -20, 120],
]


# ---------------------------------------------------------------------------
# Pure rules (no I/O) — tests depend on these names/signatures.
# ---------------------------------------------------------------------------
def initial_board() -> list[list[str]]:
    """A fresh 8x8 board with the four standard centre discs placed."""
    board = [[EMPTY] * SIZE for _ in range(SIZE)]
    board[3][3] = WHITE
    board[3][4] = BLACK
    board[4][3] = BLACK
    board[4][4] = WHITE
    return board


def _opponent(player: str) -> str:
    return WHITE if player == BLACK else BLACK


def _flips_for(board: list[list[str]], player: str, r: int, c: int) -> list[tuple[int, int]]:
    """Cells that would flip if ``player`` played at (r, c). Empty if illegal."""
    if board[r][c] != EMPTY:
        return []
    opp = _opponent(player)
    captured: list[tuple[int, int]] = []
    for dr, dc in _DIRS:
        line: list[tuple[int, int]] = []
        nr, nc = r + dr, c + dc
        while 0 <= nr < SIZE and 0 <= nc < SIZE and board[nr][nc] == opp:
            line.append((nr, nc))
            nr, nc = nr + dr, nc + dc
        # Must end on one of our own discs to bracket the run.
        if line and 0 <= nr < SIZE and 0 <= nc < SIZE and board[nr][nc] == player:
            captured.extend(line)
    return captured


def legal_moves(board: list[list[str]], player: str) -> list[tuple[int, int]]:
    """All (r, c) cells where ``player`` can legally move (outflanks ≥1 disc)."""
    out: list[tuple[int, int]] = []
    for r in range(SIZE):
        for c in range(SIZE):
            if board[r][c] == EMPTY and _flips_for(board, player, r, c):
                out.append((r, c))
    return out


def apply_move(board: list[list[str]], player: str, r: int, c: int) -> list[list[str]]:
    """Return a NEW board with ``player``'s disc at (r, c) and all flips applied.

    If the move is illegal (no flips) the board is returned unchanged (copied).
    """
    new = [row[:] for row in board]
    flips = _flips_for(board, player, r, c)
    if not flips:
        return new
    new[r][c] = player
    for fr, fc in flips:
        new[fr][fc] = player
    return new


def count(board: list[list[str]]) -> tuple[int, int]:
    """Return (black, white) disc counts."""
    black = sum(row.count(BLACK) for row in board)
    white = sum(row.count(WHITE) for row in board)
    return black, white


# ---------------------------------------------------------------------------
# Panda AI — 1-ply positional search.
# ---------------------------------------------------------------------------
def _evaluate(board: list[list[str]], player: str) -> int:
    """Positional score for ``player``: sum of weight table over owned cells,
    minus the opponent's, plus a small mobility bonus."""
    opp = _opponent(player)
    score = 0
    for r in range(SIZE):
        for c in range(SIZE):
            cell = board[r][c]
            if cell == player:
                score += _WEIGHTS[r][c]
            elif cell == opp:
                score -= _WEIGHTS[r][c]
    # Mobility: more options for us, fewer for them, is good.
    score += 3 * (len(legal_moves(board, player)) - len(legal_moves(board, opp)))
    return score


def ai_move(board: list[list[str]], player: str) -> tuple[int, int] | None:
    """Pick the panda's best move by 1-ply positional evaluation, or None if it
    has no legal move."""
    moves = legal_moves(board, player)
    if not moves:
        return None
    best = None
    best_score = None
    for r, c in moves:
        after = apply_move(board, player, r, c)
        score = _evaluate(after, player)
        if best_score is None or score > best_score:
            best_score = score
            best = (r, c)
    return best


# ---------------------------------------------------------------------------
# Raw ANSI palette (Rich markup does NOT render in raw/fullscreen mode).
# ---------------------------------------------------------------------------
_TEAL = "\x1b[38;2;45;212;191m"
_GREEN = "\x1b[38;2;158;206;106m"
_MUTE = "\x1b[38;2;107;112;137m"
_WHITEISH = "\x1b[38;2;230;233;245m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"
# cursor cell: teal background, dark foreground
_CURSOR_BG = "\x1b[48;2;45;212;191m"
_CURSOR_FG = "\x1b[38;2;13;17;23m"

_COLS = "ABCDEFGH"

_BLACK_DISC = "●"  # ●
_WHITE_DISC = "○"  # ○


def _frame(board: list[list[str]], cur: tuple[int, int], legal: list[tuple[int, int]],
           *, status: str, hint: str, show_cursor: bool = True) -> str:
    black, white = count(board)
    legal_set = set(legal)
    lines = [
        f"  {_TEAL}{_BOLD}⚫ Reversi{_RESET}   "
        f"{_WHITEISH}{_BLACK_DISC} you {black:>2}{_RESET}   "
        f"{_MUTE}{_WHITE_DISC} panda {white:>2}{_RESET}",
        "",
    ]
    # column header (A-H)
    head = "     " + " ".join(_COLS[c] for c in range(SIZE))
    lines.append(f"{_MUTE}{head}{_RESET}")
    lines.append(f"   {_MUTE}┌{'─' * (SIZE * 2 + 1)}┐{_RESET}")
    for r in range(SIZE):
        cells = []
        for c in range(SIZE):
            cell = board[r][c]
            if cell == BLACK:
                colour, glyph = _WHITEISH + _BOLD, _BLACK_DISC
            elif cell == WHITE:
                colour, glyph = _MUTE + _BOLD, _WHITE_DISC
            elif (r, c) in legal_set:
                colour, glyph = _GREEN, "·"   # faint legal-move dot
            else:
                colour, glyph = _MUTE, "·"
            if show_cursor and (r, c) == cur:
                # cursor: highlight whatever glyph sits under it
                cells.append(f"{_CURSOR_BG}{_CURSOR_FG}{glyph}{_RESET}")
            else:
                cells.append(f"{colour}{glyph}{_RESET}")
        row = " ".join(cells)
        lines.append(f" {_MUTE}{r + 1}│{_RESET} {row} {_MUTE}│{_RESET}")
    lines.append(f"   {_MUTE}└{'─' * (SIZE * 2 + 1)}┘{_RESET}")
    lines.append("")
    if status:
        lines.append("  " + status)
    if hint:
        lines.append(f"  {_MUTE}{hint}{_RESET}")
    return "\n".join(lines)


def _winner_text(board: list[list[str]]) -> str:
    black, white = count(board)
    if black > white:
        return (f"{_GREEN}{_BOLD}\U0001f3c6 You win {black}–{white}!{_RESET}"
                f"  {_MUTE}press any key{_RESET}")
    if white > black:
        return (f"{_MUTE}{_BOLD}\U0001f43c Panda wins {white}–{black}.{_RESET}"
                f"  {_TEAL}press any key{_RESET}")
    return (f"{_TEAL}{_BOLD}\U0001f91d Tie {black}–{white}.{_RESET}"
            f"  {_MUTE}press any key{_RESET}")


def _play(console: Console) -> None:
    header(console, GAME)
    from . import _realtime as rt

    if not rt.is_interactive():
        _play_fallback(console)
        return

    board = initial_board()
    human, panda = BLACK, WHITE
    cur = (3, 3)
    quit_now = False
    pass_streak = 0          # two passes in a row ends the game
    flash = ""               # transient status line (pass notices etc.)

    rt.enter_fullscreen()
    try:
        with rt.raw_mode():
            while True:
                # --- end check: board full or both players stuck -----------
                human_moves = legal_moves(board, human)
                panda_moves = legal_moves(board, panda)
                if pass_streak >= 2 or (not human_moves and not panda_moves):
                    rt.draw(_frame(board, cur, [], status=_winner_text(board),
                                   hint="", show_cursor=False))
                    rt.read_key()
                    break

                # --- human turn -------------------------------------------
                if human_moves:
                    pass_streak = 0
                    legal = human_moves
                    # keep the cursor in bounds / on a sane starting cell
                    placed = False
                    while not placed:
                        status = flash or (f"{_GREEN}your move{_RESET} "
                                           f"{_MUTE}({len(legal)} legal){_RESET}")
                        rt.draw(_frame(board, cur, legal, status=status,
                                       hint="arrows move · space/enter place "
                                            "· q quit"))
                        flash = ""
                        key = rt.read_key()
                        if key in ("q", "esc"):
                            quit_now = True
                            placed = True
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
                            if cur in legal:
                                board = apply_move(board, human, cur[0], cur[1])
                                placed = True
                            else:
                                flash = (f"{_MUTE}illegal — green dots are "
                                         f"your moves{_RESET}")
                    if quit_now:
                        break
                else:
                    pass_streak += 1
                    flash = f"{_MUTE}no legal move — you pass{_RESET}"

                # --- end check again after the human moved -----------------
                if not legal_moves(board, human) and not legal_moves(board, panda):
                    rt.draw(_frame(board, cur, [], status=_winner_text(board),
                                   hint="", show_cursor=False))
                    rt.read_key()
                    break

                # --- panda turn -------------------------------------------
                move = ai_move(board, panda)
                if move is None:
                    pass_streak += 1
                    flash = f"{_MUTE}panda has no move — panda passes{_RESET}"
                else:
                    pass_streak = 0
                    board = apply_move(board, panda, move[0], move[1])
                    flash = (f"{_MUTE}panda played "
                             f"{_COLS[move[1]]}{move[0] + 1}{_RESET}")
    finally:
        rt.exit_fullscreen()

    black, white = count(board)
    if quit_now:
        console.print(f"  [#6b7089]bye — {black}–{white}[/#6b7089]\n")
    elif black > white:
        console.print(f"  [bold #9ece6a]\U0001f3c6 You win "
                      f"{black}–{white}![/bold #9ece6a]\n")
    elif white > black:
        console.print(f"  [bold #6b7089]\U0001f43c Panda wins "
                      f"{white}–{black}.[/bold #6b7089]\n")
    else:
        console.print(f"  [bold #2dd4bf]\U0001f91d Tie "
                      f"{black}–{white}.[/bold #2dd4bf]\n")


# ---------------------------------------------------------------------------
# Non-interactive fallback (tests / piped stdin): typed "row col".
# ---------------------------------------------------------------------------
def _render_plain(console: Console, board: list[list[str]],
                  legal: list[tuple[int, int]]) -> None:
    legal_set = set(legal)
    black, white = count(board)
    console.print(f"  [#e6e9f5]● you {black}[/#e6e9f5]   "
                  f"[#6b7089]○ panda {white}[/#6b7089]")
    console.print("    " + " ".join(_COLS[c] for c in range(SIZE)))
    for r in range(SIZE):
        cells = []
        for c in range(SIZE):
            cell = board[r][c]
            if cell == BLACK:
                cells.append("[bold #e6e9f5]●[/bold #e6e9f5]")
            elif cell == WHITE:
                cells.append("[bold #6b7089]○[/bold #6b7089]")
            elif (r, c) in legal_set:
                cells.append("[#9ece6a]·[/#9ece6a]")
            else:
                cells.append("[dim]·[/dim]")
        console.print(f"  [dim]{r + 1}[/dim] " + " ".join(cells))
    console.print()


def _play_fallback(console: Console) -> None:
    board = initial_board()
    human, panda = BLACK, WHITE
    console.print("  [dim]You are ● Black. Type \"row col\" (1-8) to play; "
                  "green dots are legal moves. q to quit.[/dim]\n")

    while True:
        human_moves = legal_moves(board, human)
        panda_moves = legal_moves(board, panda)
        if not human_moves and not panda_moves:
            break

        _render_plain(console, board, human_moves)

        if human_moves:
            raw = ask_line(console, "row col:")
            low = raw.lower()
            if low in ("q", "quit", ""):
                console.print("\n  [dim]gg![/dim]")
                return
            parts = low.split()
            if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
                console.print("  [yellow]format: row col  (e.g. 3 4)[/yellow]")
                continue
            r, c = int(parts[0]) - 1, int(parts[1]) - 1
            if not (0 <= r < SIZE and 0 <= c < SIZE):
                console.print("  [yellow]row/col must be 1-8[/yellow]")
                continue
            if (r, c) not in human_moves:
                console.print("  [yellow]illegal move — pick a green dot[/yellow]")
                continue
            board = apply_move(board, human, r, c)
        else:
            console.print("  [dim]no legal move — you pass[/dim]")

        move = ai_move(board, panda)
        if move is not None:
            board = apply_move(board, panda, move[0], move[1])
            console.print(f"  [dim]panda played {_COLS[move[1]]}{move[0] + 1}[/dim]")
        elif not legal_moves(board, human):
            break

    _render_plain(console, board, [])
    black, white = count(board)
    if black > white:
        console.print(f"  [bold #9ece6a]\U0001f3c6 You win "
                      f"{black}–{white}![/bold #9ece6a]")
    elif white > black:
        console.print(f"  [bold #6b7089]\U0001f43c Panda wins "
                      f"{white}–{black}.[/bold #6b7089]")
    else:
        console.print(f"  [bold #2dd4bf]\U0001f91d Tie {black}–{white}.[/bold #2dd4bf]")


GAME = GameMeta(key="reversi", name="Reversi", emoji="⚫",
                desc="flank & flip — Othello vs the panda", play=_play)
