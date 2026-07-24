"""Nim (misère) — take stones, but don't take the *last* one.

Several piles of stones sit on the table. On your turn you take ANY number of
stones (at least one) from a SINGLE pile. Under **misère** rules the player who
is forced to take the very last stone **LOSES**.

You play against a panda that uses the provably perfect strategy, so it can
never be beaten — it even chooses whether to move first, always seating itself
in the winning position.

The rules live in pure, I/O-free functions so they can be unit-tested without a
terminal:

``nim_sum(piles)``
    The XOR of every pile size — the "nim-sum". It is the heart of Nim theory.

``best_move(piles)``
    The optimal misère move, returned as ``(pile_index, take_count)``.

Misère Nim theory (Bouton's theorem, misère variant)
----------------------------------------------------
Classify the position by the player *about to move*:

* If some pile has **2 or more** stones, the mover LOSES exactly when the
  nim-sum is 0 — identical to normal-play Nim. So the winning move is to take
  stones to drive the nim-sum to 0.
* If **every** non-empty pile has size 1, each move just removes one whole pile,
  so play is forced: the mover LOSES exactly when the count of size-1 piles is
  ODD (they are stuck taking the last stone). The winning move therefore leaves
  an ODD number of single-stone piles for the opponent.

The single subtlety is the hand-off between those two regimes. When there is
*exactly one* pile of size >= 2 (everything else is a single stone), blindly
zeroing the nim-sum can leave an EVEN number of ones, which hands the win to the
opponent. There the perfect move instead reduces the big pile to 0 or 1 so that
the number of remaining single-stone piles is ODD. ``best_move`` handles all
three cases, which is what makes the panda perfect.
"""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# Palette (ronin brand) — used as literal hex in Rich markup.
ACCENT = "#2dd4bf"   # teal — stones / brand
GOOD = "#9ece6a"     # green — you win
WARN = "#e0af68"     # amber — invalid input
ERROR = "#f7768e"    # rose — you lose
MUTE = "#6b7089"     # slate — chrome

# A few classic opening configurations; one is picked at random each game.
STARTS: tuple[tuple[int, ...], ...] = (
    (3, 4, 5),
    (1, 3, 5, 7),
    (1, 2, 3, 4, 5),
    (3, 5, 7),
    (2, 4, 6),
)


# ---------------------------------------------------------------------------
# Pure rules (no I/O) — tests depend on these names/signatures.
# ---------------------------------------------------------------------------
def nim_sum(piles: list[int]) -> int:
    """Return the nim-sum: the bitwise XOR of all pile sizes.

    A position has nim-sum 0 iff, ignoring the misère endgame, the player about
    to move is losing under perfect play.
    """
    total = 0
    for n in piles:
        total ^= n
    return total


def mover_wins(piles: list[int]) -> bool:
    """True if the player *about to move* wins with perfect misère play.

    * If any pile has >= 2 stones: a win iff the nim-sum is non-zero.
    * If every non-empty pile is a single stone: a win iff the number of those
      single-stone piles is EVEN (an odd count forces you to take the last one).
    """
    if any(n >= 2 for n in piles):
        return nim_sum(piles) != 0
    ones = sum(1 for n in piles if n == 1)
    return ones % 2 == 0


def best_move(piles: list[int]) -> tuple[int, int]:
    """Return the optimal misère-Nim move as ``(pile_index, take_count)``.

    The returned move is always legal: ``1 <= take_count <= piles[pile_index]``.
    Requires at least one stone on the table (raises ``ValueError`` otherwise).

    Strategy (see the module docstring for the theory):

    1. **Endgame** — every non-empty pile is a single stone. Play is forced:
       take one stone, leaving an ODD number of ones for the opponent when
       possible (i.e. whenever the current count of ones is even). When the
       count is already odd the position is lost, but the move is still legal.
    2. **Hand-off** — exactly one pile has >= 2 stones. Reduce that big pile to
       0 or 1 so the number of remaining single-stone piles is ODD.
    3. **Otherwise** — two or more big piles: play normal Nim and drive the
       nim-sum to 0. (If it is already 0 the position is lost; take a single
       stone to play on.)
    """
    big = [i for i, n in enumerate(piles) if n >= 2]
    ones = [i for i, n in enumerate(piles) if n == 1]

    if not big and not ones:
        raise ValueError("best_move requires at least one stone on the table")

    # 1. Endgame: all non-empty piles are single stones. Remove one whole pile;
    #    that leaves len(ones) - 1 ones — odd (a win) when len(ones) is even.
    if not big:
        return ones[0], 1

    # 2. Hand-off: exactly one big pile. Leave an ODD number of single-stone
    #    piles. If the current count of ones is even, drop the big pile to 1
    #    (count becomes odd); if odd, clear the big pile entirely (count stays
    #    odd). Either way the opponent inherits a losing all-ones position.
    if len(big) == 1:
        i = big[0]
        height = piles[i]
        if len(ones) % 2 == 0:
            return i, height - 1   # reduce to a single stone
        return i, height           # remove the whole pile

    # 3. General position: drive the nim-sum to 0 (normal-play Nim move).
    s = nim_sum(piles)
    if s != 0:
        for i, n in enumerate(piles):
            target = n ^ s
            if target < n:
                return i, n - target

    # Lost position (nim-sum already 0): play on by taking a single stone from
    # the largest pile.
    i = max(range(len(piles)), key=lambda j: piles[j])
    return i, 1


# ---------------------------------------------------------------------------
# Interactive play.
# ---------------------------------------------------------------------------
def _render(console: Console, piles: list[int]) -> None:
    """Draw the piles as 1-indexed rows of ● tokens with a count."""
    console.print()
    for idx, n in enumerate(piles, start=1):
        if n:
            tokens = " ".join("●" for _ in range(n))
            console.print(f"    [{MUTE}]{idx}[/{MUTE}]  "
                          f"[{ACCENT}]{tokens}[/{ACCENT}]   [{MUTE}]({n})[/{MUTE}]")
        else:
            console.print(f"    [{MUTE}]{idx}[/{MUTE}]  [{MUTE}]— (empty)[/{MUTE}]")
    console.print()


def _play(console: Console) -> None:
    header(console, GAME)

    piles = list(random.choice(STARTS))
    console.print(f"  [{MUTE}]Take any number of stones from one pile by typing "
                  f"\"pile count\" (e.g. 2 3 = take 3 from pile 2).\n"
                  f"  Whoever takes the LAST stone loses. q to quit.[/{MUTE}]")

    # Seat the panda in the winning position so it is genuinely unbeatable: it
    # moves first exactly when the opening is a win for the player to move.
    panda_turn = mover_wins(piles)
    if panda_turn:
        console.print(f"  [{MUTE}]The panda eyes the piles and decides to go "
                      f"first.[/{MUTE}]")
    else:
        console.print(f"  [{MUTE}]The panda waves you ahead — you go first.[/{MUTE}]")

    while True:
        _render(console, piles)

        if panda_turn:
            pile_index, take = best_move(piles)
            piles[pile_index] -= take
            stones = "stone" if take == 1 else "stones"
            console.print(f"  [bold {ACCENT}]🐼 panda[/bold {ACCENT}] takes "
                          f"[bold]{take}[/bold] {stones} from pile "
                          f"[bold]{pile_index + 1}[/bold].")
            if sum(piles) == 0:
                # The panda took the last stone, so the panda loses — you win.
                _render(console, piles)
                console.print(f"  [bold {GOOD}]🎉 The panda took the last stone "
                              f"— you win![/bold {GOOD}]\n")
                return
            panda_turn = False
            continue

        raw = ask_line(console, "pile count:")
        low = raw.lower()
        if low in ("q", "quit", ""):
            console.print(f"\n  [{MUTE}]The panda nods. Until next time.[/{MUTE}]\n")
            return

        parts = low.split()
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            console.print(f"  [{WARN}]format: pile count  (e.g. 2 3)[/{WARN}]")
            continue
        pile, count = int(parts[0]), int(parts[1])
        if not 1 <= pile <= len(piles):
            console.print(f"  [{WARN}]pile must be 1-{len(piles)}[/{WARN}]")
            continue
        height = piles[pile - 1]
        if height == 0:
            console.print(f"  [{WARN}]pile {pile} is already empty[/{WARN}]")
            continue
        if not 1 <= count <= height:
            console.print(f"  [{WARN}]take 1-{height} from pile {pile}[/{WARN}]")
            continue

        piles[pile - 1] -= count
        if sum(piles) == 0:
            # You took the last stone, so you lose.
            _render(console, piles)
            console.print(f"  [bold {ERROR}]💀 You took the last stone — the panda "
                          f"wins. (It always does.)[/bold {ERROR}]\n")
            return
        panda_turn = True


GAME = GameMeta(
    key="nim",
    name="Nim",
    emoji="🪙",
    desc="take stones — don't take the last one (vs a perfect panda)",
    play=_play,
)
