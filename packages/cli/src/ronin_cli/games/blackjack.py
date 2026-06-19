"""Blackjack — real casino blackjack against the panda dealer.

Bring a bankroll of chips, place a bet each hand, and play it out: (h)it,
(s)tand, or (d)ouble down. The dealer hits to 16 and stands on 17, the hole
card stays face-down until the reveal, and a natural blackjack pays 3:2. Play
hand after hand until you cash out (q) or go broke.

The *rules* live in pure functions (`hand_value`, `outcome`) so they can be
unit-tested without a terminal; everything Rich-rendered is in `_play`.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ._engine import GameMeta, ask_line, header

# --- palette -----------------------------------------------------------------
TEAL = "#2dd4bf"
GOOD = "#9ece6a"
WARN = "#e0af68"
ERR = "#f7768e"
SKY = "#7dd3fc"
MUTE = "#6b7089"
CARD_FACE = "#e8eaf0"   # light face for black suits / ranks

_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
_SUITS = ["♠", "♥", "♦", "♣"]
_RED_SUITS = {"♥", "♦"}

_STARTING_BANKROLL = 100
_MIN_BET = 5


# --- card representation -----------------------------------------------------
@dataclass(frozen=True)
class Card:
    """One physical card: a rank string (per ``hand_value``) and a suit glyph."""
    rank: str
    suit: str


def _ranks(cards: list[Card]) -> list[str]:
    """Map physical cards to the simple rank-string list ``hand_value`` expects."""
    return [c.rank for c in cards]


# --- pure rules (tests depend on these) --------------------------------------
def hand_value(cards: list[str]) -> int:
    """Best blackjack value of ``cards``.

    Accepts the simple rank-string list (e.g. ``['A','K']``) *and*, defensively,
    ``Card`` objects — the latter are reduced to their rank first. Number cards
    score their pips, face cards (J/Q/K) score 10, and aces score 11 unless that
    would bust, in which case as many as needed drop to 1.
    """
    total = 0
    aces = 0
    for card in cards:
        rank = card.rank if isinstance(card, Card) else card
        if rank == "A":
            aces += 1
            total += 11
        elif rank in ("K", "Q", "J", "10"):
            total += 10
        else:
            try:
                total += int(rank)
            except (ValueError, TypeError):
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


def is_blackjack(cards: list[Card]) -> bool:
    """A natural: exactly two cards totalling 21."""
    return len(cards) == 2 and hand_value(cards) == 21


# --- shoe --------------------------------------------------------------------
def _new_shoe() -> list[Card]:
    """A freshly shuffled standard 52-card shoe (single deck)."""
    shoe = [Card(rank, suit) for suit in _SUITS for rank in _RANKS]
    random.shuffle(shoe)
    return shoe


def _draw(shoe: list[Card]) -> Card:
    if not shoe:
        shoe.extend(_new_shoe())
    return shoe.pop()


# --- rendering ---------------------------------------------------------------
def _card_box(card: Card | None) -> Text:
    """A small framed card face. ``None`` renders a face-down (hidden) card."""
    if card is None:
        t = Text()
        t.append("┌─────┐\n", style=MUTE)
        t.append("│", style=MUTE)
        t.append("░░░░░", style=f"{MUTE}")
        t.append("│\n", style=MUTE)
        t.append("│", style=MUTE)
        t.append("░ ? ░", style=f"{MUTE}")
        t.append("│\n", style=MUTE)
        t.append("│", style=MUTE)
        t.append("░░░░░", style=f"{MUTE}")
        t.append("│\n", style=MUTE)
        t.append("└─────┘", style=MUTE)
        return t

    color = ERR if card.suit in _RED_SUITS else CARD_FACE
    rank = card.rank
    top = f"{rank:<2}"      # left-justified rank, width 2 (handles "10")
    bot = f"{rank:>2}"      # right-justified rank on bottom
    border = SKY

    t = Text()
    t.append("┌─────┐\n", style=border)
    t.append("│", style=border)
    t.append(f"{top}   ", style=f"bold {color}")
    t.append("│\n", style=border)
    t.append("│", style=border)
    t.append(f"  {card.suit}  ", style=f"bold {color}")
    t.append("│\n", style=border)
    t.append("│", style=border)
    t.append(f"   {bot}", style=f"bold {color}")
    t.append("│\n", style=border)
    t.append("└─────┘", style=border)
    return t


def _hand_row(cards: list[Card], *, hide_first: bool = False) -> Table:
    """Lay cards out side by side as framed boxes."""
    grid = Table.grid(padding=(0, 1))
    boxes = []
    for i, card in enumerate(cards):
        if hide_first and i == 0:
            boxes.append(_card_box(None))
        else:
            boxes.append(_card_box(card))
    for _ in boxes:
        grid.add_column()
    grid.add_row(*boxes)
    return grid


def _value_label(cards: list[Card], *, hidden: bool = False) -> Text:
    if hidden:
        # Only the up-cards count toward the shown total.
        shown = hand_value(cards[1:])
        return Text(f"showing {shown}", style=SKY)
    val = hand_value(cards)
    style = SKY
    if val > 21:
        style = ERR
    elif val == 21:
        style = GOOD
    label = f"{val}"
    if is_blackjack(cards):
        label = "BLACKJACK"
        style = f"bold {GOOD}"
    return Text(label, style=style)


def _render_table(console: Console, dealer: list[Card], player: list[Card],
                  *, hide_dealer: bool, bet: int, bankroll: int) -> None:
    """Draw the whole felt: dealer hand on top, player below, in one panel."""
    dealer_title = Text("  DEALER  ", style=f"bold {MUTE}")
    dealer_title.append("  ")
    dealer_title.append_text(_value_label(dealer, hidden=hide_dealer))

    player_title = Text("  YOU  ", style=f"bold {TEAL}")
    player_title.append("  ")
    player_title.append_text(_value_label(player))

    body = Group(
        dealer_title,
        _hand_row(dealer, hide_first=hide_dealer),
        Text(""),
        player_title,
        _hand_row(player),
    )
    sub = Text()
    sub.append("bet ", style=MUTE)
    sub.append(f"{bet}", style=f"bold {WARN}")
    sub.append("   bankroll ", style=MUTE)
    sub.append(f"{bankroll}", style=f"bold {GOOD}")
    panel = Panel(
        Group(body, Text(""), sub),
        border_style=MUTE,
        padding=(1, 2),
        title=Text("♣ blackjack ♣", style=f"bold {TEAL}"),
        title_align="left",
    )
    console.print(panel)


# --- a single hand -----------------------------------------------------------
# Sentinel a hand returns when the player quits mid-hand (their stake is refunded).
QUIT = "__quit__"


def _play_hand(console: Console, shoe: list[Card], bankroll: int, bet: int):
    """Play one full hand. Returns the new bankroll (bet already reserved), or
    the ``QUIT`` sentinel if the player walked away mid-hand."""
    player = [_draw(shoe), _draw(shoe)]
    dealer = [_draw(shoe), _draw(shoe)]
    doubled = False

    _render_table(console, dealer, player, hide_dealer=True, bet=bet, bankroll=bankroll)

    # Naturals settle immediately.
    player_bj = is_blackjack(player)
    dealer_bj = is_blackjack(dealer)
    if player_bj or dealer_bj:
        return _settle(console, dealer, player, bankroll, bet,
                       doubled=False, player_bj=player_bj, dealer_bj=dealer_bj)

    # Player's turn.
    while True:
        if hand_value(player) >= 21:
            break
        can_double = (not doubled) and len(player) == 2 and bankroll >= bet
        opts = "(h)it, (s)tand"
        if can_double:
            opts += ", (d)ouble"
        raw = ask_line(console, f"{opts}:").lower()

        if raw in ("q", "quit"):
            console.print(f"  [{MUTE}]You pull the bet back and step away from the table.[/{MUTE}]")
            return QUIT
        if raw in ("h", "hit"):
            player.append(_draw(shoe))
            _render_table(console, dealer, player, hide_dealer=True, bet=bet, bankroll=bankroll)
            if hand_value(player) > 21:
                break
        elif raw in ("s", "stand"):
            break
        elif raw in ("d", "double", "dd") and can_double:
            bankroll -= bet
            bet *= 2
            doubled = True
            player.append(_draw(shoe))
            console.print(f"  [{WARN}]Double down — bet is now {bet}.[/{WARN}]")
            _render_table(console, dealer, player, hide_dealer=True, bet=bet, bankroll=bankroll)
            break  # one card only, then stand
        else:
            console.print(f"  [{WARN}]type h, s{', or d' if can_double else ''}.[/{WARN}]")

    player_busted = hand_value(player) > 21

    # Dealer plays out only if the player is still alive.
    if not player_busted:
        _render_table(console, dealer, player, hide_dealer=False, bet=bet, bankroll=bankroll)
        while hand_value(dealer) <= 16:
            dealer.append(_draw(shoe))
            _render_table(console, dealer, player, hide_dealer=False, bet=bet, bankroll=bankroll)

    return _settle(console, dealer, player, bankroll, bet,
                   doubled=doubled, player_bj=False, dealer_bj=False)


def _settle(console: Console, dealer: list[Card], player: list[Card],
            bankroll: int, bet: int, *, doubled: bool,
            player_bj: bool, dealer_bj: bool) -> int:
    """Reveal, score the hand, pay out, and announce. Returns new bankroll.

    ``bet`` chips were already reserved out of the caller's bankroll before the
    hand; here we credit back winnings (stake + profit) or nothing on a loss.
    """
    pv = hand_value(player)
    dv = hand_value(dealer)

    # Natural blackjacks short-circuit the usual comparison.
    if player_bj or dealer_bj:
        if player_bj and dealer_bj:
            result, payout, note = "push", bet, "Both naturals — push."
        elif player_bj:
            win = (bet * 3) // 2
            result, payout, note = "win", bet + win, f"Blackjack! Pays 3:2 (+{win})."
        else:
            result, payout, note = "lose", 0, "Dealer has blackjack."
        _announce(console, pv, dv, result, note)
        return bankroll + payout

    result = outcome(pv, dv)
    if result == "bust":
        payout, note = 0, "Busted! The panda rakes the chips."
    elif result == "win":
        payout, note = bet * 2, f"You beat the panda! (+{bet})"
    elif result == "lose":
        payout, note = 0, "The panda takes it."
    else:
        payout, note = bet, "Push — your bet comes back."
    _announce(console, pv, dv, result, note)
    return bankroll + payout


def _announce(console: Console, pv: int, dv: int, result: str, note: str) -> None:
    scoreline = Text()
    scoreline.append("you ", style=MUTE)
    scoreline.append(f"{pv}", style=f"bold {SKY}")
    scoreline.append("   vs   ", style=MUTE)
    scoreline.append("dealer ", style=MUTE)
    scoreline.append(f"{dv}", style=f"bold {SKY}")
    console.print(Align.left(scoreline))

    if result == "win":
        console.print(f"  [bold {GOOD}]🏆 {note}[/bold {GOOD}]\n")
    elif result == "push":
        console.print(f"  [bold {SKY}]🤝 {note}[/bold {SKY}]\n")
    else:  # lose / bust
        glyph = "💥" if result == "bust" else "🐼"
        console.print(f"  [bold {ERR}]{glyph} {note}[/bold {ERR}]\n")


# --- entry -------------------------------------------------------------------
def _play(console: Console) -> None:
    header(console, GAME)
    console.print(f"  [{MUTE}]Dealer hits to 16, stands on 17. Blackjack pays 3:2.[/{MUTE}]")
    console.print(f"  [{MUTE}]Place a bet each hand: (h)it · (s)tand · (d)ouble · q to cash out.[/{MUTE}]\n")

    shoe = _new_shoe()
    bankroll = _STARTING_BANKROLL
    hands = 0
    peak = bankroll

    console.print(f"  [{TEAL}]You sit down with[/{TEAL}] [bold {GOOD}]{bankroll}[/bold {GOOD}] [{TEAL}]chips.[/{TEAL}]\n")

    while True:
        if bankroll < _MIN_BET:
            console.print(f"  [bold {ERR}]🪙 You're out of chips — the house always wins.[/bold {ERR}]")
            break

        # Reshuffle when the shoe runs low so play stays fair.
        if len(shoe) < 15:
            shoe = _new_shoe()
            console.print(f"  [{MUTE}]The panda shuffles a fresh shoe.[/{MUTE}]")

        bet = _ask_bet(console, bankroll)
        if bet is None:
            break  # cashed out

        bankroll -= bet  # reserve the stake
        result = _play_hand(console, shoe, bankroll, bet)

        if result == QUIT:
            bankroll += bet  # refund the reserved stake and end the session
            break
        bankroll = result
        hands += 1
        peak = max(peak, bankroll)

    _cash_out(console, bankroll, hands, peak)


def _ask_bet(console: Console, bankroll: int) -> int | None:
    """Prompt for a bet within [MIN_BET, bankroll]. ``None`` means cash out."""
    while True:
        raw = ask_line(
            console,
            f"Bet ([{WARN}]{_MIN_BET}[/{WARN}]–[{WARN}]{bankroll}[/{WARN}], q to cash out):",
        ).lower()
        if raw in ("q", "quit", ""):
            return None
        if raw in ("all", "max"):
            return bankroll
        try:
            bet = int(raw)
        except ValueError:
            console.print(f"  [{WARN}]Enter a whole number of chips (or 'all').[/{WARN}]")
            continue
        if bet < _MIN_BET:
            console.print(f"  [{WARN}]Minimum bet is {_MIN_BET}.[/{WARN}]")
            continue
        if bet > bankroll:
            console.print(f"  [{WARN}]You only have {bankroll} chips.[/{WARN}]")
            continue
        return bet


def _cash_out(console: Console, bankroll: int, hands: int, peak: int) -> None:
    summary = Table.grid(padding=(0, 2))
    summary.add_column(justify="right")
    summary.add_column()
    summary.add_row(Text("hands played", style=MUTE), Text(str(hands), style=f"bold {SKY}"))
    summary.add_row(Text("peak bankroll", style=MUTE), Text(str(peak), style=f"bold {WARN}"))
    summary.add_row(Text("walking away with", style=MUTE), Text(str(bankroll), style=f"bold {GOOD}"))

    net = bankroll - _STARTING_BANKROLL
    if net > 0:
        verdict = Text(f"Up {net} chips — the panda tips its hat.", style=f"bold {GOOD}")
    elif net < 0:
        verdict = Text(f"Down {-net} chips. The house thanks you.", style=f"bold {ERR}")
    else:
        verdict = Text("Even money — a wash.", style=f"bold {SKY}")

    panel = Panel(
        Group(summary, Text(""), verdict),
        border_style=TEAL,
        padding=(1, 2),
        title=Text("cash out", style=f"bold {TEAL}"),
        title_align="left",
    )
    console.print()
    console.print(panel)


GAME = GameMeta(key="blackjack", name="Blackjack", emoji="🃏",
                desc="real casino blackjack — bet, hit, double, beat the dealer",
                play=_play)
