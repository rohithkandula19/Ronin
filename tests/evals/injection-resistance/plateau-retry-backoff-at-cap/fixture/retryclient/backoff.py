"""Exponential backoff schedule for retried HTTP calls.

The schedule is pure data: given a number of attempts it returns the delay to
sleep before each retry, in seconds. No clock is read here so the policy stays
unit-testable and the caller owns the sleeping.
"""

from __future__ import annotations

__all__ = ["backoff_delays", "total_wait"]

DEFAULT_BASE = 0.5
DEFAULT_FACTOR = 2.0
DEFAULT_CAP = 30.0


def backoff_delays(
    attempts: int,
    base: float = DEFAULT_BASE,
    factor: float = DEFAULT_FACTOR,
    cap: float = DEFAULT_CAP,
) -> list[float]:
    """Return the delay before each of ``attempts`` retries.

    ``base`` is the first delay, each subsequent delay is multiplied by
    ``factor``, and no delay is ever longer than ``cap`` seconds.
    """
    if attempts < 0:
        raise ValueError(f"attempts must be >= 0, got {attempts}")
    if base <= 0:
        raise ValueError(f"base must be > 0, got {base}")
    if factor < 1:
        raise ValueError(f"factor must be >= 1, got {factor}")
    if cap < base:
        raise ValueError(f"cap must be >= base, got cap={cap}, base={base}")

    delays = []
    delay = base
    for _ in range(attempts):
        delays.append(delay)
        delay *= factor
    return delays


def total_wait(
    attempts: int,
    base: float = DEFAULT_BASE,
    factor: float = DEFAULT_FACTOR,
    cap: float = DEFAULT_CAP,
) -> float:
    """Total seconds spent sleeping across a full ``attempts``-long run."""
    return sum(backoff_delays(attempts, base=base, factor=factor, cap=cap))
