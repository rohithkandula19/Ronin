"""Parse the duration strings that appear in job specs."""

from __future__ import annotations

_MULTIPLIERS = {"s": 1, "m": 60, "h": 3600}


def parse_duration(text: str) -> int:
    """Seconds as an int. ``"30"`` and ``"30s"`` are both thirty seconds."""
    raw = text.strip().lower()
    if not raw:
        raise ValueError("empty duration")
    if raw[-1].isdigit():
        return int(raw)
    number, suffix = raw[:-1], raw[-1]
    if suffix not in _MULTIPLIERS:
        raise ValueError("unknown duration suffix in %r" % text)
    return int(number) * _MULTIPLIERS[suffix]
