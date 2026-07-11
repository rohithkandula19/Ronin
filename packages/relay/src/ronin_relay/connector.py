"""The laptop connector (outbound only).

This process runs on the laptop next to the Ronin gateway. It dials OUTBOUND to
the relay websocket and holds that connection open. The laptop opens no inbound
port; the firewall and router stay closed.

When a RelayRequest arrives, the connector makes ONE local HTTP call to its
single configured target_url (the Ronin gateway) and ships the result back. It
will not call any other host. There is no shell, no eval, no arbitrary URL.

The forwarding logic is pulled out as forward_one so tests can drive it with a
fake HTTP client and prove a request really reaches the local target.
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
from typing import Protocol
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx
import websockets

from .config import ConnectorConfig
from .protocol import (
    KIND_REQUEST,
    ConnectorReply,
    RelayRequest,
    encode,
)

logger = logging.getLogger("ronin_relay.connector")


def resolve_target_url(target_url: str, req_path: str) -> str:
    """Join a phone-supplied request path onto the fixed target URL, rooted to
    the target's own path.

    The phone never chooses the host. It may name a sub-path of the configured
    target, but it must not escape it: ``..`` traversal, percent-encoded
    traversal/separators (``%2e`` / ``%2f`` / ``%5c``), and backslashes are all
    rejected, and the normalized result is prefix-confined to the target root.
    Raises ``ValueError`` on any escape attempt (the caller reports it honestly
    instead of hitting an unintended endpoint). Symlink escape does not apply:
    this joins a URL path, not a filesystem path.
    """
    parts = urlsplit(target_url)
    base_path = parts.path.rstrip("/")  # e.g. "/webhooks/agent" (or "" if none)

    # Reject encoded separators / dot-encoding and backslashes before any join.
    low = req_path.lower()
    if "\\" in req_path or "%2f" in low or "%5c" in low or "%2e" in low:
        raise ValueError("encoded path traversal or separators are not allowed")

    p = urlsplit(req_path)  # split off any query/fragment; only the path is joined
    raw_path = p.path or "/"
    # Defense in depth: reject a literal parent-dir segment even after decoding.
    if ".." in unquote(raw_path).replace("\\", "/").split("/"):
        raise ValueError("path traversal ('..') is not allowed")

    normalized = posixpath.normpath(base_path + "/" + raw_path.lstrip("/"))
    confined = (
        normalized == base_path or normalized.startswith(base_path + "/")
        if base_path
        else normalized.startswith("/")
    )
    if not confined:
        raise ValueError("resolved path escapes the configured target root")

    return urlunsplit((parts.scheme, parts.netloc, normalized, p.query, p.fragment))


class LocalCaller(Protocol):
    """Anything that can perform one HTTP request and return a response.

    httpx.AsyncClient satisfies this. Tests pass a fake to avoid real network.
    """

    async def request(
        self, method: str, url: str, *, json: object, headers: dict[str, str]
    ) -> "httpx.Response": ...


async def forward_one(
    raw_message: str,
    config: ConnectorConfig,
    caller: LocalCaller,
) -> str:
    """Process one inbound websocket frame and return the reply frame as text.

    This is the heart of the connector. It is deliberately I/O-injected (the
    caller is passed in) so it is fully testable offline. A stub that does not
    actually call the target would produce the wrong status/body and fail the
    round-trip test.
    """
    message = RelayRequest.model_validate_json(raw_message)
    if message.kind != KIND_REQUEST:
        raise ValueError(f"unexpected message kind: {message.kind}")

    task = message.request
    # The URL is ALWAYS the configured target plus the request path. The phone
    # never chooses the host, and cannot traverse out of the target's path root
    # (see resolve_target_url). An escape attempt raises and is reported below.
    try:
        url = resolve_target_url(config.target_url, task.path)
    except ValueError as exc:
        return encode(ConnectorReply(id=message.id, status=0, error=f"blocked: {exc}"))

    try:
        response = await caller.request(
            task.method,
            url,
            json=task.body,
            headers=task.headers,
        )
    except Exception as exc:  # noqa: BLE001 - report any local failure honestly
        reply = ConnectorReply(
            id=message.id,
            status=0,
            error=f"{type(exc).__name__}: {exc}",
        )
        return encode(reply)

    # Decode the target body as JSON when possible, else return text.
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        body = response.text

    reply = ConnectorReply(
        id=message.id,
        status=response.status_code,
        headers={},
        body=body,
    )
    return encode(reply)


async def run_connector(config: ConnectorConfig) -> None:
    """Connect to the relay and serve forwarded requests until cancelled.

    Reconnects with a simple backoff if the relay drops. The token is sent as a
    query parameter because websocket clients cannot reliably set headers.
    """
    backoff = 1.0
    url = f"{config.relay_url}?token={config.token}"
    async with httpx.AsyncClient(timeout=config.target_timeout_seconds) as client:
        while True:
            try:
                async with websockets.connect(url) as ws:
                    logger.info("connected to relay")
                    backoff = 1.0
                    async for raw in ws:
                        reply = await forward_one(raw, config, client)
                        await ws.send(reply)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("relay connection lost (%s), retrying", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
