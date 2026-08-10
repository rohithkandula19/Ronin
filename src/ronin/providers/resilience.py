"""Retry and failover, as client wrappers rather than as code inside adapters.

Migrated from ``packages/agent-patterns/providers/`` — the ladder values and the
two hard-won semantics come from there, and the shapes are new:

* **Retry belongs outside the adapter.** In the legacy tree every adapter had its
  own copy of the ladder, so a fix in one did not reach the others. Here it wraps
  *any* ``ModelClient``, which means a new adapter gets retry for free and cannot
  get it subtly wrong.
* **Both wrappers are async and stream-aware.** The legacy versions slept on the
  event loop's thread. These take an injectable ``sleep``, which is also the only
  reason a six-attempt ladder is testable in milliseconds.

The two semantics worth preserving verbatim:

1. **A mid-stream failure is recoverable, but only with a reset.** Once tokens
   have reached the user you cannot silently restart on another model — the answer
   renders twice. Emitting ``StreamReset`` first tells the consumer to discard the
   partial, and *then* the retry is safe. The legacy failover learned this the hard
   way (it originally re-raised and lost the whole turn once a single token had
   streamed).
2. **Only retryable errors are retried.** A 401 is not a rate limit, and burning
   six attempts and sixty seconds on a bad key is worse than failing immediately.
   ``ProviderError.retryable`` is the classification, set by the transport.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace

from .base import ModelClient
from .types import Capabilities, Completed, ModelDelta, ModelRequest, ProviderError, StreamReset

#: An awaitable sleep. Injected so the tests do not actually wait sixty seconds.
Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How patiently to retry a transient failure.

    The defaults are the legacy ladder, kept because they were chosen against a
    real constraint: the waits sum to about sixty seconds across the attempts,
    which is long enough to ride over a free-tier *per-minute* rate-limit window.
    An agent loop fires several calls per task, so a turn that survives one 429 is
    a turn the user does not have to restart.
    """

    max_attempts: int = 6
    base_delay: float = 2.0
    max_delay: float = 30.0
    #: Cap on a server-supplied ``Retry-After``. A server asking us to wait an hour
    #: is a server we should give up on, not obey.
    max_retry_after: float = 60.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("RetryPolicy.max_attempts must be >= 1")
        if self.base_delay <= 0:
            raise ValueError("RetryPolicy.base_delay must be positive")
        if self.max_delay < self.base_delay:
            raise ValueError("RetryPolicy.max_delay must be >= base_delay")

    def wait_for(self, attempt: int, retry_after: str | None = None) -> float:
        """Seconds to wait before retry ``attempt`` (0-based).

        Honours a numeric ``Retry-After`` when the server sends one — it knows its
        own window better than our ladder does — and otherwise doubles from
        ``base_delay`` up to ``max_delay``: 2, 4, 8, 16, 30, 30.
        """
        if retry_after:
            try:
                return min(float(retry_after), self.max_retry_after)
            except ValueError:
                # A date-formatted Retry-After is legal HTTP and we do not parse
                # it; falling through to the ladder is better than guessing.
                pass
        return float(min(self.base_delay * (2**attempt), self.max_delay))


#: No retries at all. Useful for a fast-failing test or a local model where a
#: failure means a bug rather than a busy server.
NO_RETRY = RetryPolicy(max_attempts=1)


@dataclass(slots=True)
class RetryStats:
    """What the retry wrapper actually did, for logs and the demo."""

    attempts: int = 0
    retries: int = 0
    slept_seconds: float = 0.0
    resets_emitted: int = 0
    last_error: str = ""

    def summary(self) -> str:
        if not self.retries:
            return f"{self.attempts} attempt(s), no retries"
        return (
            f"{self.attempts} attempt(s), {self.retries} retr(ies), "
            f"{self.slept_seconds:.1f}s slept, {self.resets_emitted} reset(s)"
        )


class RetryingClient:
    """Retries transient failures around any :class:`ModelClient`.

    Wraps at the *client* level rather than the transport, because a stream can
    fail after the connection succeeded — a dropped connection mid-answer is the
    common case on a free tier, and a transport-level retry cannot see it.
    """

    def __init__(
        self,
        inner: ModelClient,
        *,
        policy: RetryPolicy | None = None,
        sleep: Sleeper | None = None,
    ) -> None:
        self._inner = inner
        self._policy = policy or RetryPolicy()
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self.stats = RetryStats()

    @property
    def inner(self) -> ModelClient:
        """The client being retried. Public so callers can identify what is wrapped."""
        return self._inner

    def capabilities(self) -> Capabilities:
        return self._inner.capabilities()

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        """Stream, retrying on retryable failures until the policy is spent."""
        policy = self._policy
        last: ProviderError | None = None

        for attempt in range(policy.max_attempts):
            self.stats.attempts += 1
            emitted = False
            finished = False
            try:
                async for delta in self._inner.stream(req):
                    if isinstance(delta, Completed):
                        finished = True
                    else:
                        emitted = True
                    yield delta
            except asyncio.CancelledError:
                # An interrupt is not a transient failure. Retrying here would
                # keep the model talking after the user asked it to stop.
                raise
            except ProviderError as exc:
                last = exc
                self.stats.last_error = str(exc)
                if not exc.retryable or attempt == policy.max_attempts - 1:
                    raise
            else:
                if finished:
                    return
                # The stream ended without a Completed. That is a truncated
                # response, not a success — treating it as one would hand the loop
                # a turn with no stop reason and no usage.
                last = ProviderError(
                    "the provider stream ended without a final message",
                    retryable=True,
                )
                self.stats.last_error = str(last)
                if attempt == policy.max_attempts - 1:
                    raise last

            if emitted:
                # See module docstring: a partial answer is already on screen, so
                # the retry is only safe once the consumer has been told to drop it.
                self.stats.resets_emitted += 1
                yield StreamReset(reason=f"retrying after: {self.stats.last_error}")

            delay = policy.wait_for(attempt, _retry_after_of(last))
            self.stats.retries += 1
            self.stats.slept_seconds += delay
            await self._sleep(delay)

        # Unreachable: the loop either returns, raises, or exhausts and raises.
        raise last or ProviderError("retry loop exhausted with no error recorded")


def _retry_after_of(error: ProviderError | None) -> str | None:
    """The server's ``Retry-After``, if the transport attached one."""
    if error is None:
        return None
    value = getattr(error, "retry_after", None)
    return value if isinstance(value, str) else None


# --------------------------------------------------------------------------- #
# Failover
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class FailoverStats:
    """Which client answered, and whether that was the primary."""

    last_used: str = ""
    failed_over_to: str = ""
    attempts: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def failed_over(self) -> bool:
        return bool(self.failed_over_to)

    def summary(self) -> str:
        if not self.last_used:
            return "no client answered"
        if not self.failed_over:
            return f"{self.last_used} (primary)"
        return f"failed over to {self.failed_over_to} after {len(self.errors)} failure(s)"


class FailoverClient:
    """Tries several clients in order; advances to the next on failure.

    A single-vendor agent dies when its one provider has an outage. This is the
    thing a single-vendor tool structurally cannot do, and it is cheap here because
    every member is already a ``ModelClient``.

    **Capabilities are the narrowest across members**, which is the subtle part.
    If the primary supports parallel tools and the fallback does not, promising
    parallel tools means the loop may dispatch a batch concurrently and then fail
    over to a client that cannot honour the ordering it already assumed. The
    narrow answer is correct for every member, so it is the only safe one.
    """

    def __init__(
        self,
        clients: Sequence[ModelClient],
        *,
        labels: Sequence[str] = (),
    ) -> None:
        if not clients:
            raise ValueError("FailoverClient needs at least one client")
        self._clients = tuple(clients)
        self._labels = tuple(labels) or tuple(f"provider-{i}" for i in range(len(clients)))
        if len(self._labels) != len(self._clients):
            raise ValueError("FailoverClient labels must be parallel to clients")
        self.stats = FailoverStats()

    def capabilities(self) -> Capabilities:
        """The intersection of every member's capabilities."""
        caps = self._clients[0].capabilities()
        for client in self._clients[1:]:
            other = client.capabilities()
            caps = replace(
                caps,
                native_tools=caps.native_tools and other.native_tools,
                parallel_tools=caps.parallel_tools and other.parallel_tools,
                prompt_cache=caps.prompt_cache and other.prompt_cache,
                thinking=caps.thinking and other.thinking,
                vision=caps.vision and other.vision,
                max_context=min(caps.max_context, other.max_context),
            )
        return caps

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        """Stream from the first client that succeeds."""
        self.stats = FailoverStats()
        errors: list[str] = []
        last: BaseException | None = None

        for index, client in enumerate(self._clients):
            label = self._labels[index]
            self.stats.attempts += 1
            emitted = False
            finished = False
            try:
                async for delta in client.stream(req):
                    if isinstance(delta, Completed):
                        finished = True
                    else:
                        emitted = True
                    yield delta
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # failing over is the entire point
                last = exc
                errors.append(f"{label}: {exc}")
                self.stats.errors = tuple(errors)
                if emitted:
                    yield StreamReset(reason=f"{label} failed mid-stream; failing over")
                continue

            if finished:
                self.stats.last_used = label
                if index > 0:
                    self.stats.failed_over_to = label
                return

            errors.append(f"{label}: stream ended without a final message")
            self.stats.errors = tuple(errors)
            if emitted:
                yield StreamReset(reason=f"{label} ended early; failing over")

        detail = "; ".join(errors)
        raise ProviderError(
            f"all {len(self._clients)} providers failed: {detail}",
            retryable=False,
        ) from last
