"""Cron matching — does a 5-field cron expression fire at a given minute?

`cron_describe.py` turns a cron expression into English; this decides whether a
expression *matches* a datetime, which is what the scheduler needs to find due
tasks. Pure and unit-tested: no network, no LLM, no clock reads (the caller
passes the datetime). Standard 5-field cron plus the common ``@aliases``.

Fields (in order): minute hour day-of-month month day-of-week.
Supported per field: ``*``, ``a``, ``a-b``, ``a,b,c``, ``*/n``, ``a-b/n``, and
comma-separated lists of those. Day-of-week is 0-6 with 0=Sunday (7 also accepted
as Sunday, the common cron convention).
"""
from __future__ import annotations

from datetime import datetime

# Shared alias table — same expansions cron_describe uses, kept local so this
# module stays import-light and standalone.
ALIASES: dict[str, str] = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

# (low, high) inclusive bounds for each of the 5 fields.
_BOUNDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]


def _parse_field(spec: str, low: int, high: int) -> set[int]:
    """Expand one cron field into the explicit set of integers it matches.

    Raises ValueError on a malformed field so ``is_valid_cron`` can report it.
    """
    values: set[int] = set()
    for piece in spec.split(","):
        piece = piece.strip()
        if not piece:
            raise ValueError(f"empty element in field {spec!r}")
        step = 1
        if "/" in piece:
            base, _, step_s = piece.partition("/")
            if not step_s.isdigit() or int(step_s) <= 0:
                raise ValueError(f"bad step in {piece!r}")
            step = int(step_s)
        else:
            base = piece

        if base == "*":
            start, stop = low, high
        elif "-" in base:
            a, _, b = base.partition("-")
            if not (a.isdigit() and b.isdigit()):
                raise ValueError(f"bad range in {piece!r}")
            start, stop = int(a), int(b)
        elif base.isdigit():
            start = stop = int(base)
        else:
            raise ValueError(f"unparseable element {piece!r}")

        if start > stop:
            raise ValueError(f"inverted range in {piece!r}")
        for v in range(start, stop + 1, step):
            if not (low <= v <= high):
                raise ValueError(f"value {v} out of range {low}-{high} in {piece!r}")
            values.add(v)
    return values


def normalize(expr: str) -> str:
    """Expand a ``@alias`` to its 5-field form (or return ``expr`` unchanged)."""
    return ALIASES.get(expr.strip(), expr.strip())


def is_valid_cron(expr: str) -> bool:
    """True iff ``expr`` is a parseable 5-field cron expression or a known alias."""
    try:
        parse_cron(expr)
        return True
    except ValueError:
        return False


def parse_cron(expr: str) -> list[set[int]]:
    """Parse a cron expression into five sets [minutes, hours, dom, month, dow].

    Day-of-week 7 is folded onto 0 (Sunday). Raises ValueError on bad input.
    """
    fields = normalize(expr).split()
    if len(fields) != 5:
        raise ValueError("expected 5 fields or a @alias")
    parsed: list[set[int]] = []
    for spec, (low, high) in zip(fields, _BOUNDS):
        # DOW accepts 7 as an alias for Sunday before bound-checking.
        if (low, high) == (0, 6):
            spec = spec.replace("7", "0")
        parsed.append(_parse_field(spec, low, high))
    return parsed


def cron_matches(expr: str, when: datetime) -> bool:
    """True iff the cron expression fires during the minute of ``when``.

    Day-of-month and day-of-week combine with OR when *both* are restricted —
    the standard Vixie-cron rule (e.g. ``0 0 1 * 1`` fires on the 1st OR on any
    Monday). When only one is restricted, only that one constrains.
    """
    minutes, hours, doms, months, dows = parse_cron(expr)
    if when.minute not in minutes:
        return False
    if when.hour not in hours:
        return False
    if when.month not in months:
        return False

    # Python weekday(): Monday=0..Sunday=6. Cron: Sunday=0..Saturday=6.
    cron_dow = (when.weekday() + 1) % 7
    dom_restricted = doms != set(range(1, 32))
    dow_restricted = dows != set(range(0, 7))
    dom_hit = when.day in doms
    dow_hit = cron_dow in dows

    if dom_restricted and dow_restricted:
        return dom_hit or dow_hit
    if dom_restricted:
        return dom_hit
    if dow_restricted:
        return dow_hit
    return True  # both wildcard
