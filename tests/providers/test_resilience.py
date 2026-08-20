"""Retry and failover: the ladder, and the two semantics worth preserving.

The load-bearing assertions here are the ones about *not rendering twice*. A retry
or a failover that restarts a stream after tokens have reached the user is a bug
the user sees, and it is the bug the legacy failover shipped with before it learned
to emit a reset first.

Every test injects ``sleep``, so a six-attempt sixty-second ladder runs instantly
and the slept seconds are asserted rather than waited out.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest

from ronin.core.types import Message, Role, Text
from ronin.providers import (
    NO_RETRY,
    Capabilities,
    Completed,
    FailoverClient,
    FinishReason,
    ModelRequest,
    ProviderError,
    RetryingClient,
    RetryPolicy,
    StreamReset,
    TextDelta,
    Usage,
)
from ronin.providers.types import ModelDelta

REQUEST = ModelRequest(model="m", system="s")

CAPS = Capabilities(
    native_tools=True,
    parallel_tools=True,
    prompt_cache=True,
    thinking=True,
    max_context=200_000,
    vision=True,
)


class Recorder:
    """Collects the delays a client was asked to sleep for."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def done(text: str = "ok") -> Completed:
    return Completed(
        message=Message(role=Role.ASSISTANT, content_blocks=(Text(text),)),
        finish=FinishReason.STOP,
        usage=Usage(input_tokens=1, output_tokens=1),
    )


class Scripted:
    """A client that raises or streams the next scripted response on each call."""

    def __init__(self, script: Sequence[Sequence[ModelDelta] | BaseException]) -> None:
        self._script = tuple(script)
        self.calls = 0
        self.caps = CAPS

    def capabilities(self) -> Capabilities:
        return self.caps

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        index = self.calls
        self.calls += 1
        if index >= len(self._script):
            raise AssertionError(f"called {index + 1}x, {len(self._script)} scripted")
        step = self._script[index]
        if isinstance(step, BaseException):
            raise step
        for delta in step:
            yield delta


async def drain(stream: AsyncIterator[ModelDelta]) -> list[ModelDelta]:
    return [delta async for delta in stream]


def transient(message: str = "429 rate limited", retry_after: str = "") -> ProviderError:
    return ProviderError(message, retryable=True, retry_after=retry_after)


def permanent(message: str = "401 bad key") -> ProviderError:
    return ProviderError(message, retryable=False)


# --------------------------------------------------------------------------- #
# The ladder
# --------------------------------------------------------------------------- #


def test_the_default_ladder_is_the_legacy_one() -> None:
    """2, 4, 8, 16, 30, 30 — patient enough to ride a per-minute rate window."""
    policy = RetryPolicy()
    waits = [policy.wait_for(attempt) for attempt in range(6)]
    assert waits == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert sum(waits) == 90.0


def test_a_numeric_retry_after_beats_the_ladder() -> None:
    """The server knows its own window better than our backoff does."""
    assert RetryPolicy().wait_for(0, "7") == 7.0
    assert RetryPolicy().wait_for(5, "3") == 3.0


def test_an_absurd_retry_after_is_capped() -> None:
    assert RetryPolicy().wait_for(0, "3600") == 60.0


def test_a_date_formatted_retry_after_falls_back_to_the_ladder() -> None:
    """HTTP allows a date there; we do not parse it rather than guess."""
    assert RetryPolicy().wait_for(1, "Wed, 21 Oct 2015 07:28:00 GMT") == 4.0


def test_an_invalid_policy_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_attempts must be >= 1"):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError, match="base_delay must be positive"):
        RetryPolicy(base_delay=0)
    with pytest.raises(ValueError, match="max_delay must be >= base_delay"):
        RetryPolicy(base_delay=10, max_delay=5)


# --------------------------------------------------------------------------- #
# Retry
# --------------------------------------------------------------------------- #


async def test_a_successful_call_is_not_retried() -> None:
    inner = Scripted([[TextDelta("hi"), done()]])
    sleeper = Recorder()
    client = RetryingClient(inner, sleep=sleeper)
    deltas = await drain(client.stream(REQUEST))
    assert inner.calls == 1
    assert sleeper.delays == []
    assert isinstance(deltas[-1], Completed)
    assert client.stats.retries == 0
    assert "no retries" in client.stats.summary()


async def test_a_transient_failure_before_any_output_is_retried_silently() -> None:
    """No tokens reached the user, so no reset is needed and none is sent."""
    inner = Scripted([transient(), [TextDelta("hi"), done()]])
    sleeper = Recorder()
    client = RetryingClient(inner, sleep=sleeper)
    deltas = await drain(client.stream(REQUEST))
    assert inner.calls == 2
    assert sleeper.delays == [2.0]
    assert not any(isinstance(d, StreamReset) for d in deltas)
    assert isinstance(deltas[-1], Completed)


async def test_a_mid_stream_failure_emits_a_reset_before_retrying() -> None:
    """The whole point: a partial answer is on screen and must be discarded."""

    class HalfThenFail:
        def __init__(self) -> None:
            self.calls = 0

        def capabilities(self) -> Capabilities:
            return CAPS

        async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
            self.calls += 1
            if self.calls == 1:
                yield TextDelta("The answer is ")
                raise transient("connection dropped")
            yield TextDelta("The answer is 42.")
            yield done("The answer is 42.")

    inner = HalfThenFail()
    sleeper = Recorder()
    client = RetryingClient(inner, sleep=sleeper)
    deltas = await drain(client.stream(REQUEST))

    kinds = [type(d).__name__ for d in deltas]
    assert kinds == ["TextDelta", "StreamReset", "TextDelta", "Completed"]
    # The reset comes *before* the second attempt's text, or the user sees both.
    assert kinds.index("StreamReset") < kinds.index("TextDelta", 1)
    assert client.stats.resets_emitted == 1
    assert sleeper.delays == [2.0]


async def test_a_permanent_failure_is_not_retried() -> None:
    """Six attempts and sixty seconds on a bad key is worse than failing fast."""
    inner = Scripted([permanent()])
    sleeper = Recorder()
    with pytest.raises(ProviderError, match="401"):
        await drain(RetryingClient(inner, sleep=sleeper).stream(REQUEST))
    assert inner.calls == 1
    assert sleeper.delays == []


async def test_the_ladder_is_exhausted_then_the_error_propagates() -> None:
    inner = Scripted([transient()] * 6)
    sleeper = Recorder()
    client = RetryingClient(inner, sleep=sleeper)
    with pytest.raises(ProviderError, match="429"):
        await drain(client.stream(REQUEST))
    assert inner.calls == 6
    # Five sleeps, not six: the last attempt fails and raises rather than waiting.
    assert sleeper.delays == [2.0, 4.0, 8.0, 16.0, 30.0]
    assert client.stats.attempts == 6


async def test_a_server_retry_after_is_honoured_per_attempt() -> None:
    inner = Scripted([transient(retry_after="5"), transient(retry_after="11"), [done()]])
    sleeper = Recorder()
    await drain(RetryingClient(inner, sleep=sleeper).stream(REQUEST))
    assert sleeper.delays == [5.0, 11.0]


async def test_a_stream_that_ends_without_a_completed_is_a_retryable_truncation() -> None:
    """A stream with no final message is not a success — the loop would get no
    stop reason and no usage, and would treat a cut-off turn as a finished one."""
    inner = Scripted([[TextDelta("half")], [TextDelta("whole"), done()]])
    sleeper = Recorder()
    client = RetryingClient(inner, sleep=sleeper)
    deltas = await drain(client.stream(REQUEST))
    assert inner.calls == 2
    assert any(isinstance(d, StreamReset) for d in deltas)
    assert isinstance(deltas[-1], Completed)


async def test_a_persistently_truncated_stream_eventually_raises() -> None:
    inner = Scripted([[TextDelta("half")]] * 2)
    client = RetryingClient(inner, policy=RetryPolicy(max_attempts=2), sleep=Recorder())
    with pytest.raises(ProviderError, match="without a final message"):
        await drain(client.stream(REQUEST))


async def test_no_retry_means_one_attempt() -> None:
    inner = Scripted([transient()])
    with pytest.raises(ProviderError):
        await drain(RetryingClient(inner, policy=NO_RETRY, sleep=Recorder()).stream(REQUEST))
    assert inner.calls == 1


async def test_an_interrupt_is_not_a_transient_failure() -> None:
    """Retrying a cancellation would keep the model talking after a Ctrl-C."""
    import asyncio

    inner = Scripted([asyncio.CancelledError()])
    sleeper = Recorder()
    with pytest.raises(asyncio.CancelledError):
        await drain(RetryingClient(inner, sleep=sleeper).stream(REQUEST))
    assert inner.calls == 1
    assert sleeper.delays == []


async def test_retry_passes_capabilities_through_unchanged() -> None:
    inner = Scripted([[done()]])
    assert RetryingClient(inner).capabilities() == CAPS


async def test_the_stats_summary_reports_what_happened() -> None:
    inner = Scripted([transient(), transient(), [done()]])
    client = RetryingClient(inner, sleep=Recorder())
    await drain(client.stream(REQUEST))
    summary = client.stats.summary()
    assert "3 attempt" in summary
    assert "2 retr" in summary
    assert "6.0s slept" in summary


# --------------------------------------------------------------------------- #
# Failover
# --------------------------------------------------------------------------- #


async def test_the_primary_answers_and_nothing_fails_over() -> None:
    primary = Scripted([[TextDelta("hi"), done()]])
    backup = Scripted([])
    client = FailoverClient([primary, backup], labels=["primary", "backup"])
    await drain(client.stream(REQUEST))
    assert primary.calls == 1
    assert backup.calls == 0
    assert client.stats.last_used == "primary"
    assert not client.stats.failed_over
    assert "(primary)" in client.stats.summary()


async def test_a_dead_primary_fails_over_to_the_backup() -> None:
    primary = Scripted([ProviderError("outage")])
    backup = Scripted([[TextDelta("hi"), done()]])
    client = FailoverClient([primary, backup], labels=["primary", "backup"])
    deltas = await drain(client.stream(REQUEST))
    assert isinstance(deltas[-1], Completed)
    assert client.stats.failed_over_to == "backup"
    assert "failed over to backup" in client.stats.summary()


async def test_failover_walks_the_whole_chain_in_order() -> None:
    first = Scripted([ProviderError("a")])
    second = Scripted([ProviderError("b")])
    third = Scripted([[done()]])
    client = FailoverClient([first, second, third], labels=["a", "b", "c"])
    await drain(client.stream(REQUEST))
    assert (first.calls, second.calls, third.calls) == (1, 1, 1)
    assert client.stats.last_used == "c"
    assert len(client.stats.errors) == 2


async def test_a_mid_stream_failure_emits_a_reset_before_failing_over() -> None:
    """The bug the legacy failover shipped with: it re-raised and lost the turn."""

    class HalfThenFail:
        def capabilities(self) -> Capabilities:
            return CAPS

        async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
            yield TextDelta("partial ")
            raise ProviderError("dropped")

    backup = Scripted([[TextDelta("complete answer"), done("complete answer")]])
    client = FailoverClient([HalfThenFail(), backup], labels=["primary", "backup"])
    deltas = await drain(client.stream(REQUEST))
    kinds = [type(d).__name__ for d in deltas]
    assert kinds == ["TextDelta", "StreamReset", "TextDelta", "Completed"]
    assert client.stats.failed_over_to == "backup"


async def test_all_providers_failing_reports_every_error() -> None:
    client = FailoverClient(
        [Scripted([ProviderError("first down")]), Scripted([ProviderError("second down")])],
        labels=["a", "b"],
    )
    with pytest.raises(ProviderError) as caught:
        await drain(client.stream(REQUEST))
    message = str(caught.value)
    assert "all 2 providers failed" in message
    assert "first down" in message
    assert "second down" in message


async def test_a_truncated_stream_counts_as_a_failure_worth_failing_over() -> None:
    primary = Scripted([[TextDelta("half")]])
    backup = Scripted([[TextDelta("whole"), done()]])
    client = FailoverClient([primary, backup], labels=["primary", "backup"])
    deltas = await drain(client.stream(REQUEST))
    assert isinstance(deltas[-1], Completed)
    assert client.stats.failed_over_to == "backup"


async def test_an_interrupt_stops_the_chain_rather_than_failing_over() -> None:
    import asyncio

    primary = Scripted([asyncio.CancelledError()])
    backup = Scripted([])
    client = FailoverClient([primary, backup])
    with pytest.raises(asyncio.CancelledError):
        await drain(client.stream(REQUEST))
    assert backup.calls == 0


async def test_capabilities_are_the_narrowest_across_the_chain() -> None:
    """A promise the fallback cannot keep is a promise the loop will act on."""
    weak = Scripted([])
    weak.caps = Capabilities(
        native_tools=True,
        parallel_tools=False,
        prompt_cache=False,
        thinking=False,
        max_context=32_768,
        vision=False,
    )
    caps = FailoverClient([Scripted([]), weak]).capabilities()
    assert caps.native_tools is True
    assert caps.parallel_tools is False
    assert caps.prompt_cache is False
    assert caps.thinking is False
    assert caps.vision is False
    assert caps.max_context == 32_768


async def test_a_single_member_chain_reports_its_own_capabilities() -> None:
    assert FailoverClient([Scripted([])]).capabilities() == CAPS


def test_an_empty_chain_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one client"):
        FailoverClient([])


def test_mismatched_labels_are_rejected() -> None:
    with pytest.raises(ValueError, match="parallel to clients"):
        FailoverClient([Scripted([]), Scripted([])], labels=["only-one"])


def test_default_labels_are_generated_when_none_are_given() -> None:
    client = FailoverClient([Scripted([]), Scripted([])])
    assert client.capabilities() == CAPS
    assert client.stats.summary() == "no client answered"


# --------------------------------------------------------------------------- #
# Retry inside failover — the combination the router builds
# --------------------------------------------------------------------------- #


async def test_each_member_is_retried_before_the_chain_advances() -> None:
    """The router wraps each member in retry, then the chain in failover, so a
    busy primary is retried rather than abandoned on the first 429."""
    primary = Scripted([transient(), transient(), [TextDelta("hi"), done()]])
    backup = Scripted([])
    sleeper = Recorder()
    chain = FailoverClient(
        [
            RetryingClient(primary, sleep=sleeper),
            RetryingClient(backup, sleep=sleeper),
        ],
        labels=["primary", "backup"],
    )
    await drain(chain.stream(REQUEST))
    assert primary.calls == 3
    assert backup.calls == 0, "a retryable primary must not be abandoned"
    assert chain.stats.last_used == "primary"
    assert sleeper.delays == [2.0, 4.0]


async def test_the_chain_advances_only_once_retries_are_exhausted() -> None:
    primary = Scripted([transient()] * 2)
    backup = Scripted([[done()]])
    chain = FailoverClient(
        [
            RetryingClient(primary, policy=RetryPolicy(max_attempts=2), sleep=Recorder()),
            RetryingClient(backup, sleep=Recorder()),
        ],
        labels=["primary", "backup"],
    )
    await drain(chain.stream(REQUEST))
    assert primary.calls == 2
    assert backup.calls == 1
    assert chain.stats.failed_over_to == "backup"
