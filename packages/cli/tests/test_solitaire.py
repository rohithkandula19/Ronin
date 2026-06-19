"""Klondike Solitaire — unit tests for the pure rule functions."""
from __future__ import annotations

import random

from ronin_cli.games.solitaire import (
    GAME,
    can_go_to_foundation,
    can_stack_tableau,
    make_deck,
)


def test_can_stack_tableau_down_and_alternating() -> None:
    # A red six on a black seven: descending by one + alternating color -> legal.
    assert can_stack_tableau((7, "spades"), (6, "hearts")) is True
    assert can_stack_tableau((7, "clubs"), (6, "diamonds")) is True
    # Same color -> illegal even though it's descending.
    assert can_stack_tableau((7, "spades"), (6, "clubs")) is False
    assert can_stack_tableau((7, "hearts"), (6, "diamonds")) is False
    # Wrong rank: a gap or ascending -> illegal.
    assert can_stack_tableau((7, "spades"), (5, "hearts")) is False
    assert can_stack_tableau((7, "spades"), (8, "hearts")) is False
    # Only a King may start an empty column.
    assert can_stack_tableau(None, (13, "hearts")) is True
    assert can_stack_tableau(None, (12, "spades")) is False


def test_can_go_to_foundation_up_by_suit() -> None:
    # An Ace (and only an Ace) starts an empty foundation.
    assert can_go_to_foundation(None, (1, "spades")) is True
    assert can_go_to_foundation(None, (2, "spades")) is False
    # +1 of the same suit climbs the foundation.
    assert can_go_to_foundation((1, "spades"), (2, "spades")) is True
    assert can_go_to_foundation((5, "hearts"), (6, "hearts")) is True
    # Wrong suit or wrong rank -> illegal.
    assert can_go_to_foundation((1, "spades"), (2, "hearts")) is False
    assert can_go_to_foundation((1, "spades"), (3, "spades")) is False


def test_make_deck_is_52_unique_and_seeded() -> None:
    deck = make_deck(random.Random(7))
    assert len(deck) == 52
    assert len(set(deck)) == 52  # 52 distinct cards
    # Every suit holds a full Ace..King run.
    for suit in ("spades", "hearts", "diamonds", "clubs"):
        assert sorted(r for r, s in deck if s == suit) == list(range(1, 14))
    # Deterministic for a given seed, and actually shuffled (not sorted order).
    assert make_deck(random.Random(7)) == deck
    assert make_deck(random.Random(8)) != deck


def test_game_meta() -> None:
    assert GAME.key == "solitaire"
    assert GAME.name == "Solitaire"
    assert GAME.emoji == "🃏"
    assert callable(GAME.play)
