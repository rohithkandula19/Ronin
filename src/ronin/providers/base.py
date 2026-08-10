"""The provider contract, and the two pieces of plumbing every HTTP adapter needs.

:class:`ModelClient` is the whole public surface of this package. Everything above
it — the loop, the router, the ledger — is written against these two methods, so a
new provider is a new file here and a line of config, never a change at a call
site.

:class:`Transport` exists so the adapters are testable. An adapter that calls
``httpx`` directly can only be tested against a live endpoint, which would break
the offline-tests rule and make the golden fixtures impossible. Instead an adapter
is handed something that yields bytes, and in tests that something reads a file.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from .types import Capabilities, ModelDelta, ModelRequest, ProviderError

# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #


@runtime_checkable
class ModelClient(Protocol):
    """A model, reduced to "stream a request" and "say what you can do"."""

    def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        """Stream one request. Exactly one ``Completed`` ends the stream.

        Not declared ``async def``: an async generator function's return type *is*
        ``AsyncIterator``, and declaring both would require callers to write
        ``await client.stream(...)`` before iterating.
        """
        ...

    def capabilities(self) -> Capabilities:
        """What this model can do. Cheap, synchronous, and never a network call."""
        ...


@runtime_checkable
class Transport(Protocol):
    """Something that turns a request into a stream of response bytes."""

    def send(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> AsyncIterator[bytes]:
        """POST ``body`` and yield the response body in arbitrary-sized chunks."""
        ...


# --------------------------------------------------------------------------- #
# Server-sent events
# --------------------------------------------------------------------------- #


class SSEReader:
    """Reassembles ``data:`` payloads from a byte stream split at any boundary.

    Chunk boundaries fall wherever the network puts them — mid-line, mid-UTF-8
    character, between ``\\r`` and ``\\n``. Every adapter would otherwise
    re-implement this, and every adapter would get the mid-character case wrong,
    so it lives here and is tested directly.
    """

    def __init__(self) -> None:
        self._pending = b""

    def feed(self, chunk: bytes) -> list[str]:
        """Return the ``data:`` payloads completed by ``chunk``, in order."""
        self._pending += chunk
        payloads: list[str] = []
        while True:
            index = self._pending.find(b"\n")
            if index < 0:
                break
            line = self._pending[:index]
            self._pending = self._pending[index + 1 :]
            payload = _data_payload(line)
            if payload is not None:
                payloads.append(payload)
        return payloads

    def finish(self) -> list[str]:
        """Flush a final line that arrived without a trailing newline."""
        line, self._pending = self._pending, b""
        payload = _data_payload(line)
        return [payload] if payload is not None else []


def _data_payload(line: bytes) -> str | None:
    """The payload of one SSE line, or None for blanks, comments and other fields."""
    text = line.rstrip(b"\r").decode("utf-8", errors="replace").strip()
    if not text or text.startswith(":"):
        return None
    if not text.startswith("data:"):
        # `event:`/`id:`/`retry:` lines carry no payload we act on. Anthropic sends
        # `event:` before every `data:`; the type is inside the JSON anyway.
        return None
    return text[len("data:") :].strip()


async def sse_json(stream: AsyncIterator[bytes]) -> AsyncIterator[Mapping[str, Any]]:
    """Decode a byte stream into SSE JSON objects, skipping ``[DONE]`` and junk.

    A payload that is not JSON is skipped rather than fatal: several gateways
    inject keep-alive text into the stream, and killing the turn over a keep-alive
    would be a self-inflicted outage.
    """
    reader = SSEReader()
    async for chunk in stream:
        for payload in reader.feed(chunk):
            event = _decode(payload)
            if event is not None:
                yield event
    for payload in reader.finish():
        event = _decode(payload)
        if event is not None:
            yield event


def _decode(payload: str) -> Mapping[str, Any] | None:
    if not payload or payload == "[DONE]":
        return None
    try:
        parsed = json.loads(payload)
    except ValueError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


# --------------------------------------------------------------------------- #
# The real transport
# --------------------------------------------------------------------------- #


class HttpTransport:
    """An ``httpx``-backed :class:`Transport`.

    ``httpx`` is imported lazily and only here, which is what keeps the rest of
    ``src/ronin`` importable with nothing installed — the whole test suite runs
    against replay transports and never touches this class.
    """

    def __init__(self, *, timeout: float = 120.0) -> None:
        self._timeout = timeout

    async def send(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> AsyncIterator[bytes]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised by absence, not tests
            raise ProviderError(
                "httpx is required to talk to a remote provider; "
                "local adapters and the test suite do not need it",
                retryable=False,
            ) from exc

        async with (
            httpx.AsyncClient(timeout=self._timeout) as client,
            client.stream("POST", url, headers=dict(headers), json=dict(body)) as resp,
        ):
            if resp.status_code >= 400:
                detail = (await resp.aread()).decode("utf-8", errors="replace")
                raise ProviderError(
                    f"{url} returned {resp.status_code}: {detail[:500]}",
                    retryable=resp.status_code in _RETRYABLE_STATUSES,
                    retry_after=resp.headers.get("retry-after", ""),
                )
            async for chunk in resp.aiter_bytes():
                yield chunk


#: Statuses worth a retry. Free tiers rate-limit constantly and an agent fires
#: several calls per task, so a retry here often saves the whole turn.
_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def join_url(base_url: str, path: str) -> str:
    """Join a configured base URL with an endpoint path.

    Tolerates the three things people actually type: a trailing slash, a missing
    ``/v1``-style suffix already being present, and a leading slash on ``path``.
    """
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def require_sequence(value: Sequence[Any], name: str) -> None:
    """Guard against a string being passed where a sequence of items is meant."""
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be a sequence of items, not a string")
