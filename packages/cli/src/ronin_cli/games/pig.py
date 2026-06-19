"""Pig (the dice game) vs the panda — press your luck, first to 100."""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

WIN = 100
PANDA_THRESHOLD = 20


def apply_roll(turn_total: int, roll: int) -> tuple[int, bool]:
    """Pure rule: fold ``roll`` into ``turn_total``.

    Returns ``(new_turn_total, busted)``. A roll of 1 busts the turn,
    resetting the turn total to 0; any 2..6 adds to it.
    """
    if roll == 1:
        return 0, True
    return turn_total + roll, False


def panda_should_hold(turn_total: int, panda_score: int, your_score: int) -> bool:
    """Pure rule: panda holds once it banks ~20, or if holding wins."""
    if panda_score + turn_total >= WIN:
        return True
    return turn_total >= PANDA_THRESHOLD


def _play(console: Console) -> None:
    header(console, GAME)
    you = 0
    panda = 0
    console.print(f"  [dim]First to {WIN} wins. (r)oll, (h)old, or q to quit.[/dim]\n")

    while True:
        # ---- your turn ----
        turn = 0
        while True:
            console.print(f"  [dim]you {you} · panda {panda} · turn {turn}[/dim]")
            choice = ask_line(console, "(r)oll or (h)old:").lower()
            if choice in ("q", "quit", ""):
                console.print("\n  [dim]Catch you next time![/dim]")
                return
            if choice in ("h", "hold"):
                you += turn
                console.print(f"  [#7dd3fc]You bank {turn}.[/#7dd3fc]\n")
                break
            if choice not in ("r", "roll"):
                console.print("  [yellow]press r or h[/yellow]")
                continue
            roll = random.randint(1, 6)
            turn, busted = apply_roll(turn, roll)
            if busted:
                console.print(f"  [red]🎲 {roll} — bust! turn lost.[/red]\n")
                break
            console.print(f"  [#9ece6a]🎲 {roll}[/#9ece6a]")
        if you >= WIN:
            console.print(f"  [bold #9ece6a]🎉 You reach {you} — you win![/bold #9ece6a]")
            return

        # ---- panda's turn ----
        console.print("  [bold]🐼 Panda's turn...[/bold]")
        turn = 0
        while True:
            if panda_should_hold(turn, panda, you):
                panda += turn
                console.print(f"  [#7dd3fc]Panda banks {turn}.[/#7dd3fc]\n")
                break
            roll = random.randint(1, 6)
            turn, busted = apply_roll(turn, roll)
            if busted:
                console.print(f"  [red]🐼 rolls {roll} — bust! turn lost.[/red]\n")
                break
            console.print(f"  [dim]🐼 rolls {roll} (turn {turn})[/dim]")
        if panda >= WIN:
            console.print(f"  [bold red]🐼 Panda reaches {panda} — panda wins![/bold red]")
            return


GAME = GameMeta(key="pig", name="Pig Dice", emoji="🎲",
                desc="press your luck — roll or hold, first to 100", play=_play)
