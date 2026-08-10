"""Replay recorded provider bytes from disk. No sockets, ever.

The fixtures under ``fixtures/`` are the real wire shapes, byte for byte. This
module feeds them to an adapter through the :class:`~ronin.providers.base.Transport`
seam, which is why the whole suite runs offline and why "identical normalized
output" is something we can actually assert rather than hope for.

:func:`splits` is the part that earns its keep. A stream that only ever arrives in
one piece never exercises the reassembly logic, and every real bug in this layer —
a tag split across chunks, a UTF-8 character split across chunks, a JSON fragment
split mid-escape — lives exactly there. So each fixture is replayed under several
chunkings and the results are required to be identical.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> bytes:
    """Read one fixture verbatim. ``name`` is like ``"anthropic/happy.sse"``."""
    path = FIXTURES / name
    if not path.exists():
        raise FileNotFoundError(f"no fixture {name!r} under {FIXTURES}")
    return path.read_bytes()


def load_tokens(name: str) -> tuple[str, ...]:
    """Read a local-model token fixture: one token per line, newlines stripped."""
    text = load(name).decode("utf-8")
    return tuple(line for line in text.split("\n") if line != "")


def chunk_by(data: bytes, size: int) -> tuple[bytes, ...]:
    """Split ``data`` into fixed-size pieces. ``size <= 0`` means one piece."""
    if size <= 0:
        return (data,)
    return tuple(data[i : i + size] for i in range(0, len(data), size))


#: The chunkings every fixture is replayed under. ``1`` is the cruel one: it puts a
#: boundary between every pair of bytes, including inside a multi-byte character
#: and inside ``\\r\\n``.
SPLIT_SIZES: tuple[int, ...] = (0, 1, 3, 7, 64, 4096)


def splits(data: bytes) -> Iterator[tuple[int, tuple[bytes, ...]]]:
    """Yield ``(size, chunks)`` for each chunking in :data:`SPLIT_SIZES`."""
    for size in SPLIT_SIZES:
        yield size, chunk_by(data, size)


class ReplayTransport:
    """A :class:`~ronin.providers.base.Transport` that yields recorded bytes.

    Records the request it was given so tests can assert on the body an adapter
    built — the request side of an adapter is as much a contract as the response
    side, and a body with the cache marker in the wrong place fails silently.
    """

    def __init__(self, chunks: Sequence[bytes]) -> None:
        self._chunks = tuple(chunks)
        self.calls: list[dict[str, Any]] = []

    @classmethod
    def from_fixture(cls, name: str, *, chunk_size: int = 0) -> ReplayTransport:
        return cls(chunk_by(load(name), chunk_size))

    async def send(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> AsyncIterator[bytes]:
        self.calls.append({"url": url, "headers": dict(headers), "body": dict(body)})
        for chunk in self._chunks:
            yield chunk

    @property
    def last_body(self) -> Mapping[str, Any]:
        if not self.calls:
            raise AssertionError("transport was never called")
        body = self.calls[-1]["body"]
        assert isinstance(body, Mapping)
        return body


class ScriptedTransport:
    """Replays a different fixture on each successive call.

    The shim's repair loop makes more than one request, so a single-response
    transport cannot test it: the second call has to return something different or
    the "did the repair work" assertion is vacuous.
    """

    def __init__(self, responses: Sequence[bytes]) -> None:
        self._responses = tuple(responses)
        self.calls: list[dict[str, Any]] = []

    async def send(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> AsyncIterator[bytes]:
        index = len(self.calls)
        self.calls.append({"url": url, "headers": dict(headers), "body": dict(body)})
        if index >= len(self._responses):
            raise AssertionError(
                f"transport called {index + 1} time(s) but only "
                f"{len(self._responses)} response(s) were scripted"
            )
        yield self._responses[index]


def token_generator(tokens: Sequence[str]) -> Any:
    """A local-model generator that replays ``tokens``, ignoring the prompt."""

    def generate(prompt: str, max_tokens: int) -> Iterator[str]:
        del prompt, max_tokens
        yield from tokens

    return generate


def scripted_token_generator(turns: Sequence[Sequence[str]]) -> Any:
    """A local-model generator that replays a *different* turn on each call.

    Needed for anything multi-turn: a generator that replays the same tool call
    every time drives the loop into its stall detector, which is correct behaviour
    but tests nothing about finishing a turn.
    """
    calls = [0]

    def generate(prompt: str, max_tokens: int) -> Iterator[str]:
        del prompt, max_tokens
        index = calls[0]
        calls[0] += 1
        if index >= len(turns):
            raise AssertionError(
                f"model called {index + 1} time(s) but only {len(turns)} turn(s) scripted"
            )
        yield from turns[index]

    return generate
