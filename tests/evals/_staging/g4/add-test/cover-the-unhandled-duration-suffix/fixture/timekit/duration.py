"""Parse human duration strings ("90s", "15m", "2h", "1d") into whole seconds.

Deliberately strict: a duration we cannot read is a configuration mistake, and
guessing at it hides the mistake until something sleeps for the wrong amount of
time.
"""

UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class DurationError(ValueError):
    """A duration string could not be read."""


def parse_duration(text: str) -> int:
    """Return the number of seconds described by *text*."""
    raw = text.strip().lower()
    if not raw:
        raise DurationError("empty duration")
    unit = raw[-1]
    if unit not in UNITS:
        raise DurationError(f"unknown duration unit: {unit!r}")
    digits = raw[:-1]
    if not digits.isdigit():
        raise DurationError(f"not a whole number: {digits!r}")
    return int(digits) * UNITS[unit]


def format_duration(seconds: int) -> str:
    """Render *seconds* with the largest unit that divides it exactly."""
    if seconds < 0:
        raise DurationError("negative duration")
    if seconds == 0:
        return "0s"
    for suffix in ("d", "h", "m"):
        size = UNITS[suffix]
        if seconds % size == 0:
            return f"{seconds // size}{suffix}"
    return f"{seconds}s"
