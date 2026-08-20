"""Retry policy. The sleeper is injected so tests never actually sleep."""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")

MAX_ATTEMPTS = 3
BASE_DELAY = 0.5


class TransientError(Exception):
    """A failure worth retrying."""


def attempt(operation: Callable[[], T], sleeper: Callable[[float], None]) -> T:
    delay = BASE_DELAY
    for number in range(1, MAX_ATTEMPTS + 1):
        try:
            return operation()
        except TransientError:
            if number == MAX_ATTEMPTS:
                raise
            sleeper(delay)
            delay *= 2
    raise AssertionError("unreachable")
