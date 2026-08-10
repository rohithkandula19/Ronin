"""Render the account balances table."""

from __future__ import annotations


def render(accounts: list[tuple[str, int]]) -> list[str]:
    """One line per account, then a separator and a totals row."""
    if not accounts:
        return []
    rows = ["%s %d" % (name, cents) for name, cents in accounts]
    width = max(len(row) for row in rows)
    total = sum(cents for _, cents in accounts)
    return [*rows, "-" * width, "TOTAL %d" % total]
