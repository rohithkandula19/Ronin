"""Pig (the dice game) vs the panda — press your luck, first to 100.

Classic Pig: on your turn you keep rolling a single die, banking the running
total — but a *1* wipes the turn and passes the dice. Hold to lock in your
points. First to 100 wins. The panda plays a simple "bank at ~20" strategy.
"""
from __future__ import annotations

import random
import time

from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ._engine import GameMeta, ask_line, header

WIN = 100
PANDA_THRESHOLD = 20

# palette
TEAL = "#2dd4bf"
GOOD = "#9ece6a"
WARN = "#e0af68"
ERR = "#f7768e"
SKY = "#7dd3fc"
MUTE = "#6b7089"


# ---------------------------------------------------------------------------
# pure rules (tested — do not change names/signatures/behavior)
# ---------------------------------------------------------------------------
def apply_roll(turn_total: int, roll: int) -> tuple[int, bool]:
    """Pure rule: fold ``roll`` into ``turn_total``.

    Returns ``(new_turn_total, busted)``. A roll of 1 busts the turn,
    resetting the turn total to 0; any 2..6 adds to it.
    """
    if roll == 1:
        return 0, True
    return turn_total + roll, False


def panda_should_hold(turn_total: int, panda_score: int, your_score: int) -> bool:
    """Pure rule: panda holds once it banks ~20, or if holding already wins.

    It also presses harder when far behind: if it trails by a lot and hasn't
    yet hit a winning bank, it keeps rolling past the usual threshold.
    """
    if panda_score + turn_total >= WIN:
        return True
    behind = your_score - panda_score
    threshold = PANDA_THRESHOLD
    if behind >= 25:
        # desperate — chase a bigger turn
        threshold = 28
    return turn_total >= threshold


# ---------------------------------------------------------------------------
# ASCII die faces — the classic pip layouts for 1..6
# ---------------------------------------------------------------------------
# Each face is exactly 3 lines, 7 cells wide; "●" marks a pip.
DIE_FACES: dict[int, list[str]] = {
    1: ["       ",
        "   ●   ",
        "       "],
    2: ["●      ",
        "       ",
        "      ●"],
    3: ["●      ",
        "   ●   ",
        "      ●"],
    4: ["●     ●",
        "       ",
        "●     ●"],
    5: ["●     ●",
        "   ●   ",
        "●     ●"],
    6: ["●     ●",
        "●     ●",
        "●     ●"],
}


def die_face(value: int) -> list[str]:
    """Return the 3-line ASCII pip layout for a die showing ``value`` (1..6)."""
    return DIE_FACES.get(value, DIE_FACES[1])


def _die_panel(value: int, *, color: str) -> Panel:
    """A boxed, bordered die showing ``value`` in the given accent color."""
    body = Text("\n".join(die_face(value)), style=f"bold {color}", justify="center")
    return Panel(
        Align.center(body, vertical="middle"),
        border_style=color,
        padding=(0, 1),
        width=11,
        height=5,
    )


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def _bar(score: int, color: str, width: int = 24) -> Text:
    filled = max(0, min(width, round(score / WIN * width)))
    t = Text()
    t.append("█" * filled, style=color)
    t.append("░" * (width - filled), style=MUTE)
    return t


def _scoreboard(you: int, panda: int, turn: int, *, turn_color: str = SKY) -> Panel:
    """A live scoreboard: both totals, progress bars, and the live turn pot."""
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="left", no_wrap=True)
    table.add_column(justify="left")
    table.add_column(justify="right", no_wrap=True)

    table.add_row(
        Text("🧑 You", style=f"bold {SKY}"),
        _bar(you, SKY),
        Text(f"{you:>3} / {WIN}", style=f"bold {SKY}"),
    )
    table.add_row(
        Text("🐼 Panda", style=f"bold {GOOD}"),
        _bar(panda, GOOD),
        Text(f"{panda:>3} / {WIN}", style=f"bold {GOOD}"),
    )
    table.add_row(
        Text("🎲 Turn", style=f"bold {turn_color}"),
        Text(f"banking {turn} this turn…", style=MUTE),
        Text(f"+{turn:>2}", style=f"bold {turn_color}"),
    )
    return Panel(
        table,
        title=f"[bold {TEAL}]PIG · first to {WIN}[/bold {TEAL}]",
        border_style=TEAL,
        padding=(1, 2),
    )


def _animate_roll(console: Console, *, color: str) -> int:
    """Show a die tumbling, then settle on the final value. Returns the roll."""
    final = random.randint(1, 6)
    # a few quick tumbling frames for a "rolling" feel
    for _ in range(6):
        face = random.randint(1, 6)
        console.print(Align.left(_die_panel(face, color=MUTE)))
        time.sleep(0.06)
        # move cursor back up over the 5 lines we just printed
        console.file.write("\x1b[5A")
        console.file.flush()
    console.print(Align.left(_die_panel(final, color=color)))
    return final


# ---------------------------------------------------------------------------
# turns
# ---------------------------------------------------------------------------
def _your_turn(console: Console, you: int, panda: int) -> tuple[int, bool]:
    """Run the human's turn. Returns ``(banked_points, quit)``."""
    turn = 0
    console.print(_scoreboard(you, panda, turn))
    console.print(
        f"  [{MUTE}]Your turn — [{GOOD}](r)oll[/{GOOD}], "
        f"[{SKY}](h)old[/{SKY}], or [{ERR}]q[/{ERR}] to quit.[/{MUTE}]"
    )
    while True:
        choice = ask_line(console, "(r)oll or (h)old:").lower()
        if choice in ("q", "quit"):
            return turn, True
        if choice in ("h", "hold"):
            console.print(
                f"  [bold {SKY}]🪙 You bank {turn} — total {you + turn}.[/bold {SKY}]\n"
            )
            return turn, False
        if choice not in ("r", "roll", ""):
            console.print(f"  [{WARN}]Press [bold]r[/bold] to roll or "
                          f"[bold]h[/bold] to hold.[/{WARN}]")
            continue

        roll = _animate_roll(console, color=SKY)
        turn, busted = apply_roll(turn, roll)
        if busted:
            console.print(
                f"  [bold {ERR}]💥 Rolled a 1 — BUST! You lose this turn's "
                f"points.[/bold {ERR}]\n"
            )
            return 0, False
        console.print(
            f"  [{GOOD}]🎲 You rolled a {roll}.[/{GOOD}]  "
            f"[{MUTE}]turn pot is now[/{MUTE}] [bold {SKY}]{turn}[/bold {SKY}]\n"
        )
        console.print(_scoreboard(you, panda, turn))


def _panda_turn(console: Console, you: int, panda: int) -> int:
    """Run the panda's automated turn. Returns the points it banked."""
    turn = 0
    console.print(f"  [bold {GOOD}]🐼 Panda's turn…[/bold {GOOD}]")
    time.sleep(0.4)
    while True:
        if panda_should_hold(turn, panda, you):
            console.print(
                f"  [bold {GOOD}]🐼 Panda holds and banks {turn} — "
                f"total {panda + turn}.[/bold {GOOD}]\n"
            )
            return turn

        console.print(f"  [{MUTE}]🐼 Panda decides to roll…[/{MUTE}]")
        time.sleep(0.3)
        roll = _animate_roll(console, color=GOOD)
        turn, busted = apply_roll(turn, roll)
        if busted:
            console.print(
                f"  [bold {ERR}]💥 Panda rolled a 1 — BUST! Its turn is "
                f"wiped.[/bold {ERR}]\n"
            )
            return 0
        console.print(
            f"  [{GOOD}]🎲 Panda rolled a {roll}.[/{GOOD}]  "
            f"[{MUTE}]panda's pot is now[/{MUTE}] [bold {GOOD}]{turn}[/bold {GOOD}]"
        )
        time.sleep(0.45)


def _win_screen(console: Console, *, you_won: bool, you: int, panda: int) -> None:
    if you_won:
        title = "🎉  YOU WIN!  🎉"
        color = GOOD
        flavor = "The panda bows in defeat. Glorious."
    else:
        title = "🐼  PANDA WINS  🐼"
        color = ERR
        flavor = "The panda did a little victory roll. Rematch?"

    big = Text(title, style=f"bold {color}", justify="center")
    score = Text(f"You {you}   ·   Panda {panda}",
                 style=f"bold {SKY}", justify="center")
    sub = Text(flavor, style=MUTE, justify="center")
    console.print()
    console.print(Panel(
        Align.center(Text("\n").join([big, score, sub])),
        border_style=color,
        padding=(1, 4),
        title=f"[bold {TEAL}]GAME OVER[/bold {TEAL}]",
    ))


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------
def _play(console: Console) -> None:
    header(console, GAME)
    console.print(
        f"  [{MUTE}]Roll to build a turn pot — but a [bold {ERR}]1[/bold {ERR}] "
        f"wipes it. Hold to bank. First to {WIN} wins.[/{MUTE}]\n"
    )

    you = 0
    panda = 0

    while True:
        banked, quit_now = _your_turn(console, you, panda)
        if quit_now:
            console.print(f"\n  [{MUTE}]Catch you next time, babe![/{MUTE}]")
            return
        you += banked
        if you >= WIN:
            _win_screen(console, you_won=True, you=you, panda=panda)
            return

        panda += _panda_turn(console, you, panda)
        if panda >= WIN:
            _win_screen(console, you_won=False, you=you, panda=panda)
            return


GAME = GameMeta(key="pig", name="Pig Dice", emoji="🎲",
                desc="press your luck — roll or hold, first to 100", play=_play)
