"""Retry policies for the object-store transfer workers.

Both directions use exponential backoff. Downloads add a small fixed skew on
top so that a fleet restarting at once does not re-hammer one CDN edge in
lockstep; uploads do not, because a multipart upload already holds a slot on
the server and staggering it buys nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

_RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class UploadRetryPolicy:
    """Backoff for multipart uploads."""

    max_attempts: int = 6
    base: float = 0.5

    def next_delay(self, attempt: int) -> float:
        """Seconds to sleep before retry `attempt` (1-based)."""
        if attempt <= 0:
            return 0.0
        delay = self.base * (2 ** (attempt - 1))
        return min(delay, 10.0)

    def should_retry(self, attempt: int, status: int) -> bool:
        if attempt >= self.max_attempts:
            return False
        return status in _RETRYABLE


@dataclass(frozen=True)
class DownloadRetryPolicy:
    """Backoff for ranged downloads."""

    max_attempts: int = 8
    base: float = 0.5
    skew: float = 0.25

    def next_delay(self, attempt: int) -> float:
        """Seconds to sleep before retry `attempt` (1-based)."""
        if attempt <= 0:
            return 0.0
        delay = self.base * (2 ** (attempt - 1)) + self.skew
        return min(delay, 30.0)

    def should_retry(self, attempt: int, status: int) -> bool:
        if attempt >= self.max_attempts:
            return False
        return status in _RETRYABLE
