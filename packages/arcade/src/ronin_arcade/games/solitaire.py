"""Solitaire — Klondike, played by typing moves at the felt.

The classic patience: seven tableau columns, a stock you turn over into a
waste, and four foundations you build up by suit from Ace to King. Stack the
tableau *down* in alternating colors, parachute Kings into the gaps, and feed
every card home. Clear all four foundations to win.

Card representation
-------------------
A card is a plain ``(rank, suit)`` tuple — ``rank`` is an ``int`` 1–13 (1 = Ace,
11 = Jack, 12 = Queen, 13 = King) and ``suit`` is one of the strings
``"spades"``, ``"hearts"``, ``"diamonds"``, ``"clubs"``. Tuples are hashable, so
a deck doubles as a set for uniqueness checks. ``hearts``/``diamonds`` are red.

The *rules* live in pure, side-effect-free functions (:func:`can_stack_tableau`,
:func:`can_go_to_foundation`, :func:`make_deck`) so they can be unit-tested
without a terminal; everything Rich-rendered and stateful is in :func:`_play`.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ._engine import GameMeta, ask_line, header

# --- palette -----------------------------------------------------------------
ACCENT = "#2dd4bf"   # teal — brand
GOOD = "#9ece6a"     # soft green
WARN = "#e0af68"     # soft amber
ERR = "#f7768e"      # soft rose
MUTE = "#6b7089"     # muted slate
RED = "#f7768e"      # red suits (♥ ♦)
BLACK = "#c0caf5"    # black suits (♠ ♣) — a readable light, not hard white

SUITS = ("spades", "hearts", "diamonds", "clubs")
RED_SUITS = frozenset({"hearts", "diamonds"})
SUIT_GLYPH = {"spades": "♠", "hearts": "♥", "diamonds": "♦", "clubs": "♣"}
_RANK_LABEL = {1: "A", 11: "J", 12: "Q", 13: "K"}


def _label(rank: int) -> str:
    """Short rank label: 1→A, 11→J, 12→Q, 13→K, otherwise the number."""
    return _RANK_LABEL.get(rank, str(rank))


def _is_red(suit: str) -> bool:
    return suit in RED_SUITS


# --- pure rules (tests depend on these) --------------------------------------
def can_stack_tableau(card_on_top, moving_card) -> bool:
    """May ``moving_card`` be placed on a tableau pile whose top is ``card_on_top``?

    Tableau columns build *down* in *alternating colors*: the moving card must be
    exactly one rank lower than the card it lands on and the opposite color. An
    empty column (``card_on_top is None``) accepts only a King.
    """
    rank, suit = moving_card
    if card_on_top is None:
        return rank == 13
    top_rank, top_suit = card_on_top
    return rank == top_rank - 1 and _is_red(suit) != _is_red(top_suit)


def can_go_to_foundation(foundation_top, card) -> bool:
    """May ``card`` be played to a foundation whose top is ``foundation_top``?

    Foundations build *up by suit*: the card must be the same suit and exactly
    one rank higher. An empty foundation (``foundation_top is None``) accepts
    only an Ace.
    """
    rank, suit = card
    if foundation_top is None:
        return rank == 1
    top_rank, top_suit = foundation_top
    return suit == top_suit and rank == top_rank + 1


def make_deck(rng: random.Random) -> list:
    """A full 52-card deck — one ``(rank, suit)`` per rank 1–13 and suit — shuffled
    in place by the passed :class:`random.Random` (so a seed gives a stable deal)."""
    deck = [(rank, suit) for suit in SUITS for rank in range(1, 14)]
    rng.shuffle(deck)
    return deck


# --- game state --------------------------------------------------------------
@dataclass
class _State:
    """The whole table. Each tableau cell is a mutable ``[card, face_up]`` pair so
    a face-down card can be flipped in place when it is exposed."""
    tableau: list
    stock: list
    waste: list = field(default_factory=list)
    foundations: dict = field(default_factory=lambda: {s: [] for s in SUITS})
    moves: int = 0


def _deal(deck: list) -> tuple[list, list]:
    """Deal a Klondike layout from ``deck`` (left untouched). Column *i* gets
    *i+1* cards with only its last card face up; the remaining 24 form the stock."""
    cards = list(deck)
    tableau = [[] for _ in range(7)]
    for i in range(7):
        for j in range(i + 1):
            tableau[i].append([cards.pop(), j == i])  # only the last is face up
    return tableau, cards  # whatever is left is the stock


def _face_up_start(pile: list) -> int:
    """Index of the first face-up card in a tableau pile (len if none)."""
    for i, (_card, up) in enumerate(pile):
        if up:
            return i
    return len(pile)


def _is_run(cards: list) -> bool:
    """Is ``cards`` a valid descending, alternating-color sequence (top→bottom)?"""
    return all(can_stack_tableau(a, b) for a, b in zip(cards, cards[1:]))


def _flip_if_needed(pile: list) -> None:
    """Turn a freshly exposed top card face up."""
    if pile and not pile[-1][1]:
        pile[-1][1] = True


def _foundation_top(st: _State, suit: str):
    pile = st.foundations[suit]
    return pile[-1] if pile else None


def _is_won(st: _State) -> bool:
    return all(len(st.foundations[s]) == 13 for s in SUITS)


def _name(card) -> str:
    rank, suit = card
    return f"{_label(rank)}{SUIT_GLYPH[suit]}"


# --- moves (return (ok, message); mutate state only on success) --------------
def _draw_stock(st: _State) -> tuple[bool, str]:
    if st.stock:
        st.waste.append(st.stock.pop())
        return True, ""
    if st.waste:
        # Turn the waste back over to reform the stock (same draw order).
        st.stock = list(reversed(st.waste))
        st.waste = []
        return True, "Turned the waste back into the stock."
    return False, "The stock and waste are both empty."


def _waste_to_foundation(st: _State) -> tuple[bool, str]:
    if not st.waste:
        return False, "The waste is empty."
    card = st.waste[-1]
    if can_go_to_foundation(_foundation_top(st, card[1]), card):
        st.foundations[card[1]].append(st.waste.pop())
        return True, ""
    return False, f"{_name(card)} can't go home to the {card[1]} foundation yet."


def _waste_to_tableau(st: _State, m: int) -> tuple[bool, str]:
    if not (0 <= m < 7):
        return False, "No such column."
    if not st.waste:
        return False, "The waste is empty."
    card = st.waste[-1]
    top = st.tableau[m][-1][0] if st.tableau[m] else None
    if can_stack_tableau(top, card):
        st.tableau[m].append([st.waste.pop(), True])
        return True, ""
    return False, f"{_name(card)} can't stack on column {m + 1}."


def _tableau_to_foundation(st: _State, n: int) -> tuple[bool, str]:
    if not (0 <= n < 7):
        return False, "No such column."
    pile = st.tableau[n]
    if not pile:
        return False, f"Column {n + 1} is empty."
    card = pile[-1][0]
    if can_go_to_foundation(_foundation_top(st, card[1]), card):
        st.foundations[card[1]].append(pile.pop()[0])
        _flip_if_needed(pile)
        return True, ""
    return False, f"{_name(card)} can't go home to the {card[1]} foundation yet."


def _tableau_to_tableau(st: _State, n: int, m: int) -> tuple[bool, str]:
    if not (0 <= n < 7 and 0 <= m < 7):
        return False, "No such column."
    if n == m:
        return False, "Source and destination are the same column."
    src = st.tableau[n]
    if not src:
        return False, f"Column {n + 1} is empty."
    fu = _face_up_start(src)
    face_up = [c for c, _up in src[fu:]]
    dst = st.tableau[m]
    dst_top = dst[-1][0] if dst else None
    # Find the deepest face-up card whose run can legally land on the destination.
    for k in range(len(face_up)):
        if can_stack_tableau(dst_top, face_up[k]) and _is_run(face_up[k:]):
            moving = src[fu + k:]
            dst.extend([[c, True] for c, _up in moving])
            del src[fu + k:]
            _flip_if_needed(src)
            return True, ""
    return False, f"No legal move from column {n + 1} onto column {m + 1}."


def _autoplay(st: _State) -> tuple[bool, str]:
    """Send every card that can immediately go home to the foundations."""
    moved = 0
    changed = True
    while changed:
        changed = False
        if st.waste:
            card = st.waste[-1]
            if can_go_to_foundation(_foundation_top(st, card[1]), card):
                st.foundations[card[1]].append(st.waste.pop())
                moved += 1
                changed = True
                continue
        for pile in st.tableau:
            if pile:
                card = pile[-1][0]
                if can_go_to_foundation(_foundation_top(st, card[1]), card):
                    st.foundations[card[1]].append(pile.pop()[0])
                    _flip_if_needed(pile)
                    moved += 1
                    changed = True
    if moved:
        return True, f"Sent {moved} card{'s' if moved != 1 else ''} home."
    return False, "Nothing can go to the foundations right now."


# --- command parsing ---------------------------------------------------------
def _parse_loc(tok: str) -> tuple:
    """Parse a location token into ``(kind, index)``.

    ``w``/``waste`` → waste, ``f``/``fN``/``foundation`` → foundation (routed by
    suit, so the index is advisory), ``tN``/``cN`` or a bare number → tableau
    column N (1-based). Anything else → ``(None, None)``.
    """
    tok = tok.strip().lower()
    if tok in ("w", "waste"):
        return ("waste", None)
    if tok in ("f", "found", "foundation", "home"):
        return ("foundation", None)
    if tok and tok[0] == "f" and tok[1:].isdigit():
        return ("foundation", int(tok[1:]) - 1)
    if tok and tok[0] in ("t", "c") and tok[1:].isdigit():
        return ("tableau", int(tok[1:]) - 1)
    if tok.isdigit():
        return ("tableau", int(tok) - 1)
    return (None, None)


def _dispatch(st: _State, src: tuple, dst: tuple) -> tuple[bool, str]:
    skind, sidx = src
    dkind, didx = dst
    if skind == "waste" and dkind == "foundation":
        return _waste_to_foundation(st)
    if skind == "waste" and dkind == "tableau":
        return _waste_to_tableau(st, didx)
    if skind == "tableau" and dkind == "foundation":
        return _tableau_to_foundation(st, sidx)
    if skind == "tableau" and dkind == "tableau":
        return _tableau_to_tableau(st, sidx, didx)
    return False, "Unknown move — try 'd', 'w f', 'w t3', 't1 f', or 't2 t5'. 'h' for help."


# --- rendering ---------------------------------------------------------------
def _back() -> Text:
    return Text("▒▒▒", style=MUTE)


def _card_text(card, up: bool = True) -> Text:
    if not up:
        return _back()
    rank, suit = card
    col = RED if suit in RED_SUITS else BLACK
    return Text(f"{_label(rank):>2}{SUIT_GLYPH[suit]}", style=f"bold {col}")


def _top_zone(st: _State) -> Panel:
    stock = Text("STOCK\n", style=f"bold {MUTE}")
    if st.stock:
        stock.append_text(_back())
        stock.append(f"  {len(st.stock)}", style=MUTE)
    else:
        stock.append("↺ empty", style=MUTE)

    waste = Text("WASTE\n", style=f"bold {MUTE}")
    if st.waste:
        waste.append_text(_card_text(st.waste[-1]))
    else:
        waste.append("—", style=MUTE)

    fnd = Text("FOUNDATIONS\n", style=f"bold {MUTE}")
    for i, s in enumerate(SUITS):
        if i:
            fnd.append("   ")
        pile = st.foundations[s]
        if pile:
            fnd.append_text(_card_text(pile[-1]))
        else:
            col = RED if s in RED_SUITS else MUTE
            fnd.append(f"{SUIT_GLYPH[s]}·", style=col)

    grid = Table.grid(padding=(0, 5))
    grid.add_column()
    grid.add_column()
    grid.add_column()
    grid.add_row(stock, waste, fnd)
    return Panel(grid, border_style=MUTE, padding=(0, 2),
                 title=Text("♣ solitaire ♣", style=f"bold {ACCENT}"), title_align="left")


def _tableau_table(st: _State) -> Table:
    t = Table(box=box.SIMPLE, padding=(0, 1), pad_edge=False, collapse_padding=True)
    for i in range(7):
        t.add_column(f"T{i + 1}", justify="center", header_style=f"bold {ACCENT}")
    height = max((len(p) for p in st.tableau), default=0) or 1
    for r in range(height):
        row = []
        for i in range(7):
            pile = st.tableau[i]
            if r < len(pile):
                card, up = pile[r]
                row.append(_card_text(card, up))
            elif r == 0 and not pile:
                row.append(Text("·", style=MUTE))  # an empty column waiting for a King
            else:
                row.append(Text(" "))
        t.add_row(*row)
    return t


def _render(console: Console, st: _State) -> None:
    console.print()
    console.print(_top_zone(st))
    console.print(_tableau_table(st))
    info = Text("  moves ", style=MUTE)
    info.append(str(st.moves), style=f"bold {ACCENT}")
    info.append("    home ", style=MUTE)
    info.append(f"{sum(len(st.foundations[s]) for s in SUITS)}/52", style=f"bold {GOOD}")
    console.print(info)


def _show_help(console: Console) -> None:
    rows = [
        ("d", "draw from the stock (turns the waste back over when empty)"),
        ("w f", "send the waste card up to its foundation"),
        ("w t3", "move the waste card onto tableau column 3"),
        ("t1 f", "send column 1's top card up to its foundation"),
        ("t2 t5", "move a face-up run from column 2 onto column 5"),
        ("a", "auto-send every available card to the foundations"),
        ("q", "quit"),
    ]
    g = Table.grid(padding=(0, 3))
    g.add_column(justify="right", style=f"bold {ACCENT}")
    g.add_column(style=MUTE)
    for k, v in rows:
        g.add_row(k, v)
    note = Text(
        "Tableau builds DOWN in alternating colors; empty columns take only Kings.\n"
        "Foundations build UP by suit, Ace→King. Clear all four to win.",
        style=MUTE,
    )
    console.print(Panel(Group(g, Text(""), note), border_style=ACCENT, padding=(1, 2),
                        title=Text("how to play", style=f"bold {ACCENT}"), title_align="left"))


def _win(console: Console, st: _State) -> None:
    body = Text(f"Every column cleared in {st.moves} moves. The deck bows to you. 🎉",
                style=f"bold {GOOD}")
    console.print()
    console.print(Panel(body, border_style=GOOD, padding=(1, 2),
                        title=Text("solved", style=f"bold {ACCENT}"), title_align="left"))


# --- entry -------------------------------------------------------------------
def _play(console: Console) -> None:
    header(console, GAME)
    console.print(f"  [{MUTE}]Build the tableau DOWN in alternating colors; "
                  f"foundations UP by suit, Ace→King.[/{MUTE}]")
    console.print(
        f"  [{MUTE}]Moves:[/{MUTE}] "
        f"[{ACCENT}]d[/{ACCENT}] draw · "
        f"[{ACCENT}]w f[/{ACCENT}]/[{ACCENT}]w t3[/{ACCENT}] waste · "
        f"[{ACCENT}]t1 f[/{ACCENT}] · [{ACCENT}]t2 t5[/{ACCENT}] · "
        f"[{ACCENT}]a[/{ACCENT}] auto · [{ACCENT}]h[/{ACCENT}] help · "
        f"[{ACCENT}]q[/{ACCENT}] quit\n"
    )

    tableau, stock = _deal(make_deck(random.Random()))
    st = _State(tableau=tableau, stock=stock)
    _render(console, st)

    while True:
        if _is_won(st):
            _win(console, st)
            return

        raw = ask_line(console, "move:")
        parts = raw.split()
        if not parts or parts[0].lower() in ("q", "quit", "exit"):
            home = sum(len(st.foundations[s]) for s in SUITS)
            console.print(f"  [{MUTE}]Folded the deck — {home}/52 home. See you next deal.[/{MUTE}]")
            return

        cmd = parts[0].lower()
        if cmd in ("h", "help", "?"):
            _show_help(console)
            continue

        try:
            if cmd in ("d", "draw", "deal", "stock"):
                ok, msg = _draw_stock(st)
            elif cmd in ("a", "auto"):
                ok, msg = _autoplay(st)
            else:
                src = _parse_loc(parts[0])
                dst = _parse_loc(parts[1]) if len(parts) > 1 else (None, None)
                ok, msg = _dispatch(st, src, dst)
        except Exception:  # never crash the felt on a fat-fingered command
            ok, msg = False, "Couldn't parse that move — type 'h' for help."

        if ok:
            st.moves += 1
            _render(console, st)
            if msg:
                console.print(f"  [{MUTE}]{msg}[/{MUTE}]")
        else:
            console.print(f"  [{WARN}]{msg}[/{WARN}]")


GAME = GameMeta(key="solitaire", name="Solitaire", emoji="🃏",
                desc="Klondike — clear the tableau to the foundations", play=_play)
