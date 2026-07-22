"""In-memory token-bucket rate limiter (dev). Swap for a distributed backend in prod.

Deterministic: the caller supplies `now` (epoch seconds), so tests are exact and
there is no wall-clock dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RateLimit:
    capacity: int       # burst
    refill_per_sec: float  # sustained rate


class RateLimitExceeded(RuntimeError):
    def __init__(self, key: str, retry_after: float) -> None:
        super().__init__(f"rate limit exceeded for {key!r}; retry after {retry_after:.2f}s")
        self.key = key
        self.retry_after = retry_after


@dataclass
class _Bucket:
    tokens: float
    last: float


class RateLimiter:
    """Per-key token buckets. Keys are typically 'endpoint:user' or 'endpoint:ip'."""

    def __init__(self, default: RateLimit) -> None:
        self._default = default
        self._limits: dict[str, RateLimit] = {}
        self._buckets: dict[str, _Bucket] = {}

    def configure(self, endpoint: str, limit: RateLimit) -> None:
        self._limits[endpoint] = limit

    def _limit_for(self, endpoint: str) -> RateLimit:
        return self._limits.get(endpoint, self._default)

    def check(self, endpoint: str, subject: str, *, now: float, cost: float = 1.0) -> None:
        """Consume `cost` tokens or raise RateLimitExceeded. Fail closed on abuse."""
        limit = self._limit_for(endpoint)
        key = f"{endpoint}:{subject}"
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=float(limit.capacity), last=now)
            self._buckets[key] = bucket
        # refill
        elapsed = max(0.0, now - bucket.last)
        bucket.tokens = min(limit.capacity, bucket.tokens + elapsed * limit.refill_per_sec)
        bucket.last = now
        if bucket.tokens < cost:
            needed = cost - bucket.tokens
            retry_after = needed / limit.refill_per_sec if limit.refill_per_sec > 0 else float("inf")
            raise RateLimitExceeded(key, retry_after)
        bucket.tokens -= cost

    def allow(self, endpoint: str, subject: str, *, now: float, cost: float = 1.0) -> bool:
        try:
            self.check(endpoint, subject, now=now, cost=cost)
            return True
        except RateLimitExceeded:
            return False
