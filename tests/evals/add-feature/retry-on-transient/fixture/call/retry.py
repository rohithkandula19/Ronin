"""Retry policy. The sleeper is injected so tests never actually sleep."""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")

MAX_ATTEMPTS = 3
BASE_DELAY = 0.5


class TransientError(Exception):
    """A failure worth retrying."""


def attempt(operation: Callable[[], T], sleeper: Callable[[float], None]) -> T:
    return operation()
