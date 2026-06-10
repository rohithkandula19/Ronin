"""Detect and trim runaway repetition in model output.

Some open models (notably gpt-oss on fast inference backends) fall into a
generation loop and emit the same paragraph over and over. These helpers let a
streaming provider notice that mid-stream and stop early, and let any provider
trim a looped tail off the final text. Both are pure and unit-tested.
"""
from __future__ import annotations

_UNIT = 160       # characters of trailing context used as the "did this recur?" probe
_REPEATS = 3      # how many times that probe must appear before we call it a loop


def looks_repetitive(text: str, *, unit: int = _UNIT, repeats: int = _REPEATS) -> bool:
    """True if ``text`` ends in a block that has already appeared several times —
    i.e. the model is looping. Conservative: needs a wordy probe of length
    ``unit`` to occur at least ``repeats`` times. Pure."""
    if len(text) < unit * repeats:
        return False
    probe = text[-unit:]
    if not any(c.isalpha() for c in probe):   # ignore runs of dashes/whitespace/etc.
        return False
    return text.count(probe) >= repeats


def collapse_repetition(text: str, *, unit: int = _UNIT, repeats: int = _REPEATS) -> str:
    """If ``text`` ends with a looped block, keep the first full occurrence and
    replace the rest with a short marker. Otherwise return ``text`` unchanged. Pure."""
    if not looks_repetitive(text, unit=unit, repeats=repeats):
        return text
    probe = text[-unit:]                       # the trailing slice of one looped block
    first = text.find(probe)
    second = text.find(probe, first + 1)
    if second == -1:
        return text
    period = second - first                    # length of one repetition
    unit_start = max(0, first - period + unit)  # where the first looped block begins
    kept = text[:unit_start + period].rstrip()  # legit prefix + exactly one copy
    return kept + "\n\n…(ronin trimmed a runaway repetition loop)…"
