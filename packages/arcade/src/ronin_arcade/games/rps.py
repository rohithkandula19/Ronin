"""Rock-Paper-Scissors — a best-of-7 match against the panda.

The rules live in the pure :func:`judge` function (unit-tested), while the
interactive match (ASCII reveal, the "rock... paper... scissors... shoot!"
beat, score and history) lives in :func:`_play`. An optional toggle plays the
extended Rock-Paper-Scissors-Lizard-Spock variant.
"""
from __future__ import annotations

import random
import time

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# --- palette -----------------------------------------------------------------
TEAL = "#2dd4bf"
GOOD = "#9ece6a"
ERR = "#f7768e"
WARN = "#e0af68"
SKY = "#7dd3fc"
MUTE = "#6b7089"

# --- moves -------------------------------------------------------------------
_NAMES = {"r": "rock", "p": "paper", "s": "scissors", "l": "lizard", "k": "spock"}
_EMOJI = {"r": "✊", "p": "✋", "s": "✌", "l": "🦎", "k": "🖖"}

# Classic: what each move beats.
_BEATS = {"r": "s", "p": "r", "s": "p"}

# Lizard-Spock: each move beats two others (key -> set of beaten moves).
_BEATS_LS = {
    "r": {"s", "l"},   # rock crushes scissors, crushes lizard
    "p": {"r", "k"},   # paper covers rock, disproves spock
    "s": {"p", "l"},   # scissors cuts paper, decapitates lizard
    "l": {"p", "k"},   # lizard eats paper, poisons spock
    "k": {"s", "r"},   # spock smashes scissors, vaporizes rock
}

# A human-readable "verb" for each (winner, loser) pairing — pure flavour.
_VERBS = {
    ("r", "s"): "rock crushes scissors",
    ("r", "l"): "rock crushes lizard",
    ("p", "r"): "paper covers rock",
    ("p", "k"): "paper disproves Spock",
    ("s", "p"): "scissors cut paper",
    ("s", "l"): "scissors decapitate lizard",
    ("l", "p"): "lizard eats paper",
    ("l", "k"): "lizard poisons Spock",
    ("k", "s"): "Spock smashes scissors",
    ("k", "r"): "Spock vaporizes rock",
}

# --- ASCII art ---------------------------------------------------------------
_ART = {
    "r": [
        "    _______    ",
        "---'   ____)   ",
        "      (_____)  ",
        "      (_____)  ",
        "      (____)   ",
        "---.__(___)    ",
    ],
    "p": [
        "     _______   ",
        "---'    ____)__",
        "           ____)",
        "          _____)",
        "         (____) ",
        "---.__(___)     ",
    ],
    "s": [
        "    _______    ",
        "---'   ____)__ ",
        "          ______)",
        "       __________)",
        "      (____)     ",
        "---.__(___)      ",
    ],
    "l": [
        "      __        ",
        "  ___( o)>      ",
        "  \\ <_. )       ",
        "   `---'        ",
        "    lizard      ",
        "                ",
    ],
    "k": [
        "    \\\\//        ",
        "    _||_        ",
        "   ( oo )       ",
        "    \\__/        ",
        "    Spock       ",
        "  live long     ",
    ],
}


def judge(player: str, panda: str) -> str:
    """Pure rule (player perspective): ``"win"`` / ``"lose"`` / ``"tie"``.

    Inputs are single-letter move keys like ``'r'``, ``'p'``, ``'s'`` (and, for
    the extended variant, ``'l'`` lizard / ``'k'`` spock). Handles both the
    classic and lizard-spock move sets transparently.
    """
    if player == panda:
        return "tie"
    # Prefer the classic 3-move table when both moves are classic.
    if player in _BEATS and panda in _BEATS:
        return "win" if _BEATS[player] == panda else "lose"
    return "win" if panda in _BEATS_LS.get(player, set()) else "lose"


def _normalize(raw: str, *, lizard_spock: bool) -> str | None:
    """Map user input to a canonical move key, or ``None`` if invalid."""
    val = raw.strip().lower()
    allowed = set("rpsl") | {"k"} if lizard_spock else set("rps")
    aliases = {
        "rock": "r", "paper": "p", "scissors": "s", "scissor": "s",
        "lizard": "l", "spock": "k",
    }
    if val in aliases:
        val = aliases[val]
    if val in _NAMES and val in allowed:
        return val
    return None


def _verb(winner: str, loser: str) -> str:
    """Flavour text for who beat whom (player-agnostic)."""
    return _VERBS.get((winner, loser), f"{_NAMES[winner]} beats {_NAMES[loser]}")


def _move_panel(move: str, color: str) -> list[str]:
    """Coloured ASCII art lines for a move."""
    return [f"[{color}]{line}[/{color}]" for line in _ART[move]]


def _reveal(console: Console, you: str, panda: str, verdict: str) -> None:
    """The dramatic 'shoot!' reveal: two hands side-by-side with art."""
    you_color = {"win": GOOD, "lose": MUTE, "tie": SKY}[verdict]
    panda_color = {"win": MUTE, "lose": ERR, "tie": SKY}[verdict]

    left = _move_panel(you, you_color)
    right = _move_panel(panda, panda_color)
    console.print()
    console.print(f"      [bold {you_color}]YOU[/bold {you_color}]"
                  f"                    [bold {panda_color}]PANDA[/bold {panda_color}]")
    for l, r in zip(left, right):
        # Pad the left art block to a fixed visual width.
        console.print(f"  {l:<{15 + len(you_color) * 2 + 7}}      {r}")
    console.print(
        f"   [{you_color}]{_EMOJI[you]} {_NAMES[you]}[/{you_color}]"
        f"   [bold {MUTE}]vs[/bold {MUTE}]   "
        f"[{panda_color}]{_EMOJI[panda]} {_NAMES[panda]}[/{panda_color}]"
    )


def _countdown(console: Console) -> None:
    """rock... paper... scissors... shoot! — a snappy beat."""
    for word in ("rock", "paper", "scissors"):
        console.print(f"  [bold {TEAL}]{word}...[/bold {TEAL}]")
        time.sleep(0.3)
    console.print(f"  [bold {WARN}]SHOOT![/bold {WARN}]")
    time.sleep(0.2)


def _scoreboard(console: Console, you: int, panda: int, target: int) -> None:
    pips_you = "●" * you + "○" * (target - you)
    pips_panda = "●" * panda + "○" * (target - panda)
    console.print(
        f"  [{MUTE}]score[/{MUTE}]  "
        f"[{GOOD}]you {you}[/{GOOD}] [{GOOD}]{pips_you}[/{GOOD}]"
        f"   [{MUTE}]:[/{MUTE}]   "
        f"[{ERR}]{pips_panda}[/{ERR}] [{ERR}]{panda} panda[/{ERR}]"
        f"   [{MUTE}](first to {target})[/{MUTE}]"
    )


def _history(console: Console, rounds: list[tuple[str, str, str]]) -> None:
    """Compact per-round log: emojis + outcome marker."""
    if not rounds:
        return
    console.print(f"  [{MUTE}]history[/{MUTE}]")
    for i, (you, panda, verdict) in enumerate(rounds, 1):
        mark, color = {
            "win": ("W", GOOD),
            "lose": ("L", ERR),
            "tie": ("·", SKY),
        }[verdict]
        console.print(
            f"   [{MUTE}]{i:>2}[/{MUTE}]  "
            f"[{color}]{_EMOJI[you]}[/{color}] [{MUTE}]vs[/{MUTE}] "
            f"[{color}]{_EMOJI[panda]}[/{color}]  "
            f"[bold {color}]{mark}[/bold {color}]"
        )


def _ask_variant(console: Console) -> bool:
    """Returns True for Lizard-Spock, False for classic. Defaults to classic."""
    raw = ask_line(
        console,
        f"[{SKY}]mode?[/{SKY}] [{MUTE}](enter) classic  /  type 'ls' for "
        f"Lizard-Spock[/{MUTE}]",
    )
    return raw.strip().lower() in ("ls", "lizard-spock", "lizardspock", "spock", "5")


def _win_screen(console: Console, won: bool, you: int, panda: int) -> None:
    console.print()
    if won:
        console.print(f"  [bold {GOOD}]╔══════════════════════════════════════╗[/bold {GOOD}]")
        console.print(f"  [bold {GOOD}]║   🏆  MATCH WON — {you} : {panda}            ║[/bold {GOOD}]")
        console.print(f"  [bold {GOOD}]║   The panda bows in defeat. 🐼      ║[/bold {GOOD}]")
        console.print(f"  [bold {GOOD}]╚══════════════════════════════════════╝[/bold {GOOD}]")
    else:
        console.print(f"  [bold {ERR}]╔══════════════════════════════════════╗[/bold {ERR}]")
        console.print(f"  [bold {ERR}]║   🐼  MATCH LOST — {you} : {panda}           ║[/bold {ERR}]")
        console.print(f"  [bold {ERR}]║   The panda grins. Rematch?         ║[/bold {ERR}]")
        console.print(f"  [bold {ERR}]╚══════════════════════════════════════╝[/bold {ERR}]")


def _play(console: Console) -> None:
    header(console, GAME)

    lizard_spock = _ask_variant(console)
    pool = "rpslk" if lizard_spock else "rps"
    target = 4  # best-of-7 -> first to 4

    if lizard_spock:
        console.print(
            f"  [{MUTE}]Lizard-Spock mode. Type r/p/s/l/k or full words "
            f"(lizard, spock). First to {target}. (q to quit)[/{MUTE}]\n"
        )
    else:
        console.print(
            f"  [{MUTE}]Type r/p/s (or rock/paper/scissors). First to "
            f"{target}. (q to quit)[/{MUTE}]\n"
        )

    you = 0
    panda = 0
    rounds: list[tuple[str, str, str]] = []
    round_no = 1

    while you < target and panda < target:
        console.print(f"  [bold {TEAL}]── round {round_no} ──[/bold {TEAL}]")
        prompt = "your move?" if lizard_spock else "rock, paper or scissors?"
        raw = ask_line(console, prompt)
        if raw.strip().lower() in ("q", "quit", ""):
            console.print(f"\n  [{MUTE}]The panda waves goodbye.[/{MUTE}]")
            return

        move = _normalize(raw, lizard_spock=lizard_spock)
        if move is None:
            hint = "r, p, s, l or k" if lizard_spock else "r, p or s"
            console.print(f"  [{WARN}]try {hint}[/{WARN}]\n")
            continue

        _countdown(console)
        pmove = random.choice(pool)
        verdict = judge(move, pmove)

        _reveal(console, move, pmove, verdict)

        if verdict == "win":
            you += 1
            win, lose = move, pmove
            console.print(
                f"\n  [bold {GOOD}]✔ you win the round[/bold {GOOD}]  "
                f"[{MUTE}]({_verb(win, lose)})[/{MUTE}]"
            )
        elif verdict == "lose":
            panda += 1
            win, lose = pmove, move
            console.print(
                f"\n  [bold {ERR}]✘ panda wins the round[/bold {ERR}]  "
                f"[{MUTE}]({_verb(win, lose)})[/{MUTE}]"
            )
        else:
            console.print(f"\n  [bold {SKY}]= tie — same move, replay[/bold {SKY}]")

        rounds.append((move, pmove, verdict))
        console.print()
        _scoreboard(console, you, panda, target)
        console.print()
        round_no += 1

    _history(console, rounds)
    _win_screen(console, won=you > panda, you=you, panda=panda)


GAME = GameMeta(
    key="rps",
    name="Rock Paper Scissors",
    emoji="✊",
    desc="best of 7 vs the panda",
    play=_play,
)
