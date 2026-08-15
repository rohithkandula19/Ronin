"""Mid-session OAuth refresh-on-401, end to end and offline.

Three seams meet here: the transport must surface a ``401`` as :class:`Unauthorized` (not a
dead connection), the client must swap in a fresh bearer and re-send the *same* request, and
the driver must force a refresh even when its own clock still thinks the token is valid (a
server revocation looks exactly like that). The integration test drives the real
:class:`HttpTransport` against a sender that rejects the first ``tools/call`` and accepts it
only once the new bearer arrives — so the header actually travelling to the wire is asserted,
not assumed.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest

from ronin.mcp.client import McpClient
from ronin.mcp.config import AuthKind, McpServerConfig, TransportKind
from ronin.mcp.jsonrpc import Request
from ronin.mcp.oauth import OAuthError, TokenSet
from ronin.mcp.oauth_driver import (
    HttpFetcher,
    HttpReply,
    InMemoryAuthStore,
    OAuthDriver,
    PersistedAuth,
    token_refresher,
)
from ronin.mcp.transport import (
    HttpTransport,
    SseTransport,
    StdioTransport,
    Transport,
    Unauthorized,
    memory_duplex,
)

RESOURCE = "https://mcp.example.com/mcp"
TOKEN_ENDPOINT = "https://auth.example.com/token"
_ASM = {
    "issuer": "https://auth.example.com",
    "authorization_endpoint": "https://auth.example.com/authorize",
    "token_endpoint": TOKEN_ENDPOINT,
    "registration_endpoint": "https://auth.example.com/register",
    "code_challenge_methods_supported": ["S256"],
}
_PRM = {"resource": RESOURCE, "authorization_servers": ["https://auth.example.com"]}


def _config() -> McpServerConfig:
    return McpServerConfig(
        name="remote", transport=TransportKind.HTTP, url=RESOURCE, auth=AuthKind.OAUTH
    )


# --------------------------------------------------------------------------- #
# driver: force_refresh + the refresher hook
# --------------------------------------------------------------------------- #


class _RefreshFetcher(HttpFetcher):
    """A fetcher that only answers what a refresh needs: metadata GET + the token POST."""

    def __init__(self, token: Mapping[str, object]) -> None:
        self._token = token
        self.form_posts: list[Mapping[str, str]] = []

    async def get(self, url: str) -> HttpReply:
        body = _PRM if "oauth-protected-resource" in url else _ASM
        return HttpReply(200, json.dumps(body).encode())

    async def post_form(self, url: str, form: Mapping[str, str]) -> HttpReply:
        self.form_posts.append(dict(form))
        return HttpReply(200, json.dumps(self._token).encode())

    async def post_json(self, url: str, body: Mapping[str, object]) -> HttpReply:
        raise AssertionError("a refresh never registers a client")


def _driver(fetcher: _RefreshFetcher, store: InMemoryAuthStore) -> OAuthDriver:
    return OAuthDriver(
        fetcher=fetcher,
        store=store,
        now=lambda: 1000.0,
        new_verifier=lambda: "v" * 43,
        new_state=lambda: "s",
    )


async def test_force_refresh_refreshes_even_a_not_yet_expired_token() -> None:
    store = InMemoryAuthStore()
    # expires far in the future — ensure() would hand this back untouched; force_refresh must not.
    store.save(
        "remote",
        PersistedAuth(
            client_id="cid",
            token=TokenSet("old", RESOURCE, refresh_token="rt-1", expires_at=9_999_999.0),
        ),
    )
    fetcher = _RefreshFetcher({"access_token": "new", "token_type": "Bearer", "expires_in": 3600})
    driver = _driver(fetcher, store)

    fresh = await driver.force_refresh(_config())

    assert fresh.access_token == "new"
    assert fetcher.form_posts[-1]["grant_type"] == "refresh_token"
    assert fetcher.form_posts[-1]["refresh_token"] == "rt-1"
    saved = store.load("remote")
    assert saved is not None and saved.token is not None and saved.token.access_token == "new"


async def test_force_refresh_refuses_without_a_refresh_token() -> None:
    store = InMemoryAuthStore()
    store.save("remote", PersistedAuth(client_id="cid", token=TokenSet("old", RESOURCE)))
    driver = _driver(_RefreshFetcher({}), store)
    with pytest.raises(OAuthError, match="nothing to refresh"):
        await driver.force_refresh(_config())


async def test_token_refresher_returns_a_header_then_none_on_failure() -> None:
    store = InMemoryAuthStore()
    store.save(
        "remote",
        PersistedAuth(client_id="cid", token=TokenSet("old", RESOURCE, refresh_token="rt-1")),
    )
    fetcher = _RefreshFetcher({"access_token": "new", "token_type": "Bearer"})
    refresh = token_refresher(_driver(fetcher, store), _config())

    assert await refresh() == {"Authorization": "Bearer new"}

    store.save("remote", PersistedAuth(client_id="cid", token=TokenSet("old", RESOURCE)))  # no rt
    assert await refresh() is None  # unrefreshable → None, never an exception


# --------------------------------------------------------------------------- #
# transport: a 401 is Unauthorized, not death; the header can be swapped
# --------------------------------------------------------------------------- #


class _AuthAwareSender:
    """Answers JSON-RPC over one POST each; ``tools/call`` needs the fresh bearer.

    Rejects a ``tools/call`` (with a 401 → :class:`Unauthorized`) until the request carries
    ``Authorization: Bearer new``; ``initialize`` / ``tools/list`` always succeed. Records
    every Authorization header it saw so a test can prove the swap reached the wire.
    """

    def __init__(self) -> None:
        self.auth_seen: list[str] = []
        self.tool_calls = 0

    async def post(
        self, *, url: str, headers: Mapping[str, str], body: Mapping[str, Any]
    ) -> AsyncIterator[bytes]:
        self.auth_seen.append(headers.get("authorization", headers.get("Authorization", "")))
        method = body.get("method")
        if method == "initialize":
            yield self._reply(
                body,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "remote", "version": "1"},
                },
            )
            return
        if method == "notifications/initialized":
            yield b""
            return
        if method == "tools/list":
            yield self._reply(
                body,
                {
                    "tools": [
                        {"name": "lookup", "description": "d", "inputSchema": {"type": "object"}}
                    ]
                },
            )
            return
        if method == "tools/call":
            self.tool_calls += 1
            bearer = headers.get("authorization", headers.get("Authorization", ""))
            if bearer != "Bearer new":
                raise Unauthorized("401", challenge='Bearer error="invalid_token"')
            yield self._reply(body, {"content": [{"type": "text", "text": "ok"}], "isError": False})
            return
        raise AssertionError(f"unexpected method {method!r}")

    @staticmethod
    def _reply(body: Mapping[str, Any], result: Mapping[str, Any]) -> bytes:
        return json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result}).encode()


def _http_client(sender: _AuthAwareSender, refresher: Any) -> McpClient:
    def provider(config: McpServerConfig) -> Transport:
        return HttpTransport(url=config.url, sender=sender, headers={"Authorization": "Bearer old"})

    return McpClient(_config(), provider, refresher=refresher)


async def test_http_transport_reports_a_401_as_unauthorized_and_stays_alive() -> None:
    transport = HttpTransport(url=RESOURCE, sender=_AuthAwareSender(), headers={})
    with pytest.raises(Unauthorized):
        await transport.send(Request(method="tools/call", params={}, id=1))
    assert transport.alive  # a rejected bearer is not a dead connection


class _Raise:
    """An async iterator whose first step raises — an async-gen without an unreachable yield."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __aiter__(self) -> _Raise:
        return self

    async def __anext__(self) -> bytes:
        raise self._exc


class _UnauthorizedSseSender:
    def post(self, *, url: str, headers: Mapping[str, str], body: Mapping[str, Any]) -> _Raise:
        return _Raise(Unauthorized("401"))


async def test_sse_transport_reports_a_401_as_unauthorized_and_stays_alive() -> None:
    transport = SseTransport(url=RESOURCE, sender=_UnauthorizedSseSender(), headers={})
    with pytest.raises(Unauthorized):
        await transport.send(Request(method="tools/call", params={}, id=1))
    assert transport.alive
    transport.set_auth_header({"Authorization": "Bearer new"})  # merges, does not raise


async def test_stdio_set_auth_header_is_a_noop() -> None:
    near, _far = memory_duplex()
    transport = StdioTransport(streams=near)
    transport.set_auth_header({"Authorization": "Bearer x"})  # a stdio server sees no 401
    assert transport.alive


async def test_call_tool_refreshes_on_401_and_succeeds() -> None:
    sender = _AuthAwareSender()

    async def refresher() -> Mapping[str, str]:
        return {"Authorization": "Bearer new"}

    client = _http_client(sender, refresher)
    await client.connect()
    await client.list_tools()

    result = await client.call_tool("lookup", {"query": "x"})

    assert result.ok and result.content == "ok"
    assert sender.tool_calls == 2  # the first 401'd, the retry (with the new bearer) succeeded
    assert "Bearer new" in sender.auth_seen  # the swapped header really travelled


async def test_call_tool_gives_a_reauthorize_message_when_no_refresher() -> None:
    client = _http_client(_AuthAwareSender(), None)
    await client.connect()
    await client.list_tools()

    result = await client.call_tool("lookup", {"query": "x"})

    assert not result.ok
    assert "re-authoriz" in result.error.lower() and "log in" in result.error.lower()
    assert client.alive  # the server is up; only the authorization is gone


async def test_call_tool_gives_a_reauthorize_message_when_refresh_fails() -> None:
    async def refresher() -> None:
        return None  # the token could not be refreshed

    client = _http_client(_AuthAwareSender(), refresher)
    await client.connect()
    await client.list_tools()

    result = await client.call_tool("lookup", {"query": "x"})
    assert not result.ok and "401" in result.error


async def test_call_tool_gives_up_when_the_refreshed_token_is_also_rejected() -> None:
    sender = _AuthAwareSender()

    async def refresher() -> Mapping[str, str]:
        return {"Authorization": "Bearer still-wrong"}  # server will 401 this too

    client = _http_client(sender, refresher)
    await client.connect()
    await client.list_tools()

    result = await client.call_tool("lookup", {"query": "x"})
    assert not result.ok and "re-authoriz" in result.error.lower()
    assert sender.tool_calls == 2  # bounded: one refresh + one retry, then it stops
