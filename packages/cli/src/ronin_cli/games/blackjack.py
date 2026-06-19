"""Blackjack — hit or stand, beat the panda dealer to 21."""
from __future__ import annotations

import random

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# A standard single-deck rank multiset; suits are flavour only.
_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def hand_value(cards: list[str]) -> int:
    """Best blackjack value of ``cards``.

    Number cards score their pips, face cards (J/Q/K) score 10, and aces
    score 11 unless that would bust, in which case as many as needed drop to 1.
    """
    total = 0
    aces = 0
    for card in cards:
        if card == "A":
            aces += 1
            total += 11
        elif card in ("K", "Q", "J", "10"):
            total += 10
        else:
            try:
                total += int(card)
            except ValueError:
                continue
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def outcome(player: int, dealer: int) -> str:
    """Result from the player's perspective: win/lose/push/bust."""
    if player > 21:
        return "bust"
    if dealer > 21:
        return "win"
    if player > dealer:
        return "win"
    if player < dealer:
        return "lose"
    return "push"


def _new_deck() -> list[str]:
    deck = _RANKS * 4
    random.shuffle(deck)
    return deck


def _draw(deck: list[str]) -> str:
    # Reshuffle on the (extremely unlikely) chance the shoe runs dry.
    if not deck:
        deck.extend(_new_deck())
    return deck.pop()


def _show(console: Console, who: str, cards: list[str], *, hide: bool = False) -> None:
    if hide:
        shown = f"{cards[0]} ??"
        console.print(f"  [#6b7089]{who}:[/#6b7089] {shown}")
    else:
        shown = " ".join(cards)
        console.print(f"  [#6b7089]{who}:[/#6b7089] {shown}  [#7dd3fc]({hand_value(cards)})[/#7dd3fc]")


def _play(console: Console) -> None:
    header(console, GAME)
    console.print("  [#6b7089]Dealer hits to 16, stands on 17. (q to quit)[/#6b7089]\n")

    deck = _new_deck()
    player = [_draw(deck), _draw(deck)]
    dealer = [_draw(deck), _draw(deck)]

    _show(console, "dealer", dealer, hide=True)
    _show(console, "you", player)
    console.print()

    player_busted = False
    while True:
        if hand_value(player) >= 21:
            break
        raw = ask_line(console, "(h)it or (s)tand:").lower()
        if raw in ("q", "quit", ""):
            console.print("\n  [#6b7089]The panda racks the chips. See you next time.[/#6b7089]")
            return
        if raw in ("h", "hit"):
            player.append(_draw(deck))
            _show(console, "you", player)
            if hand_value(player) > 21:
                player_busted = True
                break
        elif raw in ("s", "stand"):
            break
        else:
            console.print("  [#e0af68]type h to hit or s to stand[/#e0af68]")

    pv = hand_value(player)
    console.print()

    if not player_busted:
        # Dealer reveals and plays out: hit while 16 or less, stand on 17+.
        _show(console, "dealer", dealer)
        while hand_value(dealer) <= 16:
            dealer.append(_draw(deck))
            _show(console, "dealer", dealer)
        console.print()

    dv = hand_value(dealer)
    result = outcome(pv, dv)
    console.print(f"  [#6b7089]you {pv}  vs  dealer {dv}[/#6b7089]")

    if result == "bust":
        console.print("  [bold #f7768e]💥 Busted! The panda wins.[/bold #f7768e]")
    elif result == "win":
        console.print("  [bold #9ece6a]🏆 You beat the panda![/bold #9ece6a]")
    elif result == "lose":
        console.print("  [bold #f7768e]🐼 The panda takes it.[/bold #f7768e]")
    else:
        console.print("  [bold #7dd3fc]🤝 Push — nobody wins.[/bold #7dd3fc]")


GAME = GameMeta(key="blackjack", name="Blackjack", emoji="🃏",
                desc="hit or stand, beat the dealer to 21", play=_play)
