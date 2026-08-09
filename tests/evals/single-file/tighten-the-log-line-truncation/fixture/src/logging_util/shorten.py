"""Shorten log fields so a rendered line fits inside a fixed column budget.

The renderer gives each field a hard character budget and pads the rest of the
row, so a field that comes back over budget pushes the row past the terminal
width and the table stops lining up.

    >>> truncate("a very long branch name indeed", 22)
    'a very long branch nam…'
"""

from __future__ import annotations

ELLIPSIS = "…"


def truncate(text: str, limit: int = 40) -> str:
    """Shorten `text` so it fits in `limit` characters.

    >>> truncate("ronin evaluates tool calls", 12)
    'ronin evalua…'
    >>> truncate("short", 12)
    'short'
    >>> len(truncate("ronin evaluates tool calls", 12))
    13
    """
    if limit <= 0:
        raise ValueError(f"limit must be positive: {limit!r}")
    if len(text) <= limit:
        return text
    return text[:limit] + ELLIPSIS


def summarise(fields: dict[str, str], limit: int = 40) -> str:
    """Render `fields` as `key=value` pairs with every value truncated.

    >>> summarise({"task": "rewrite the pagination helper", "id": "a1"}, 16)
    'task=rewrite the pagi…, id=a1'
    """
    return ", ".join(f"{key}={truncate(value, limit)}" for key, value in fields.items())
