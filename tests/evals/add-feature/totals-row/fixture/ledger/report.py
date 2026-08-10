"""Render the account balances table."""

from __future__ import annotations


def render(accounts: list[tuple[str, int]]) -> list[str]:
    """One line per account, in the order given."""
    return ["%s %d" % (name, cents) for name, cents in accounts]
