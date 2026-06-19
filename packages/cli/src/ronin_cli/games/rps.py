"""Rock-Paper-Scissors — best-of-5 against the panda."""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

_NAMES = {"r": "rock", "p": "paper", "s": "scissors"}
_EMOJI = {"r": "✊", "p": "✋", "s": "✌️"}
# what each move beats
_BEATS = {"r": "s", "p": "r", "s": "p"}


def judge(player: str, panda: str) -> str:
    """Pure rule (player perspective): ``"win"`` / ``"lose"`` / ``"tie"``."""
    if player == panda:
        return "tie"
    return "win" if _BEATS[player] == panda else "lose"


def _normalize(raw: str) -> str | None:
    """Map user input to a canonical move key, or ``None`` if invalid."""
    val = raw.strip().lower()
    if val in _NAMES:
        return val
    for key, name in _NAMES.items():
        if val == name:
            return key
    return None


def _play(console: Console) -> None:
    header(console, GAME)
    console.print("  [#6b7089]Type r/p/s (or rock/paper/scissors). First to 3 wins. (q to quit)[/#6b7089]\n")
    you = 0
    panda = 0
    while you < 3 and panda < 3:
        raw = ask_line(console, "rock, paper or scissors?")
        if raw.lower() in ("q", "quit", ""):
            console.print("\n  [#6b7089]The panda waves goodbye.[/#6b7089]")
            return
        move = _normalize(raw)
        if move is None:
            console.print("  [#e0af68]try r, p or s[/#e0af68]")
            continue
        pmove = random.choice("rps")
        verdict = judge(move, pmove)
        if verdict == "win":
            you += 1
            tag = "[#9ece6a]you win the round[/#9ece6a]"
        elif verdict == "lose":
            panda += 1
            tag = "[#f7768e]panda wins the round[/#f7768e]"
        else:
            tag = "[#7dd3fc]tie[/#7dd3fc]"
        console.print(
            f"  you {_EMOJI[move]} {_NAMES[move]}  vs  "
            f"panda {_EMOJI[pmove]} {_NAMES[pmove]}   {tag}"
        )
        console.print(f"  [#6b7089]score — you {you} : {panda} panda[/#6b7089]\n")

    if you > panda:
        console.print("  [bold #9ece6a]🏆 You took the match! The panda bows.[/bold #9ece6a]")
    else:
        console.print("  [bold #f7768e]🐼 The panda wins the match. Rematch?[/bold #f7768e]")


GAME = GameMeta(key="rps", name="Rock Paper Scissors", emoji="✊",
                desc="best of 5 vs the panda", play=_play)
