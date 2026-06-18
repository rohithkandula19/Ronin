"""Auth and rate limiting for the relay.

Auth is a single shared bearer token compared in constant time. This is
deliberately simple: the relay is small and self-hosted, and one strong random
token is enough to keep the public internet out. TLS (see the README) protects
the token in transit; do not run the relay on plain HTTP in production.
"""

from __future__ import annotations

import hmac
import time
from collections import deque


def extract_bearer(authorization: str | None) -> str | None:
    """Pull the token out of an 'Authorization: Bearer <token>' header."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def token_matches(presented: str | None, expected: str) -> bool:
    """Constant time comparison so we do not leak the token via timing."""
    if not presented:
        return False
    return hmac.compare_digest(presented, expected)


class RateLimiter:
    """A small in-memory sliding window limiter.

    No database. Suitable for a single relay process on a tiny VM. Keyed by an
    arbitrary identity string (we key by the bearer token, so one token cannot
    drown out another).
    """

    def __init__(self, max_events: int, window_seconds: float) -> None:
        self._max = max_events
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def allow(self, identity: str, now: float | None = None) -> bool:
        """Return True if this identity may proceed, recording the hit."""
        current = time.monotonic() if now is None else now
        bucket = self._hits.setdefault(identity, deque())
        cutoff = current - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self._max:
            return False
        bucket.append(current)
        return True
