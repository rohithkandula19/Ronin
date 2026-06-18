"""End to end round-trip test through the real relay app.

This is the test the doctrine demands: a phone request must GENUINELY traverse
relay -> connector -> local target -> back. We run the real relay ASGI app,
drive the phone request through httpx against that app, and run a real
in-process connector that pumps the relay websocket through the real
forward_one logic against a fake local target. No external network.

A stub relay that faked the response without going through the websocket would
fail here, because the only thing that ever produces the echo body is the fake
target reached through forward_one.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ronin_relay.connector import forward_one
from ronin_relay.relay import create_app

from .conftest import TEST_TOKEN
from .fakes import FakeLocalCaller


class _ASGIWebSocket:
    """A minimal in-process websocket client speaking ASGI to the relay app.

    This lets the connector run on the SAME event loop as the relay (no thread
    bridging), so the phone request and the connector pump are genuinely
    concurrent. It implements just enough of the websocket protocol for the
    connector handshake plus text send and receive.
    """

    def __init__(self, app, path: str) -> None:
        self._app = app
        self._path = path
        self._to_app: asyncio.Queue = asyncio.Queue()
        self._from_app: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._accepted = asyncio.Event()
        self._closed = False

    async def __aenter__(self) -> "_ASGIWebSocket":
        scope = {
            "type": "websocket",
            "path": self._path.split("?", 1)[0],
            "raw_path": self._path.encode(),
            "query_string": (
                self._path.split("?", 1)[1].encode() if "?" in self._path else b""
            ),
            "headers": [],
            "subprotocols": [],
        }
        self._task = asyncio.create_task(self._app(scope, self._receive, self._send))
        await self._to_app.put({"type": "websocket.connect"})
        await self._accepted.wait()
        return self

    async def __aexit__(self, *exc) -> None:
        self._closed = True
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    async def _receive(self) -> dict:
        return await self._to_app.get()

    async def _send(self, message: dict) -> None:
        if message["type"] == "websocket.accept":
            self._accepted.set()
        elif message["type"] == "websocket.close":
            self._accepted.set()
            await self._from_app.put({"type": "close", "code": message.get("code")})
        elif message["type"] == "websocket.send":
            await self._from_app.put({"type": "text", "text": message["text"]})

    async def send_text(self, text: str) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": text})

    async def receive_text(self) -> str:
        msg = await self._from_app.get()
        if msg["type"] == "close":
            raise WebSocketDisconnect(code=msg.get("code"))
        return msg["text"]


@pytest.mark.asyncio
async def test_phone_request_round_trips_to_local_target(relay_config, connector_config):
    app = create_app(relay_config)
    caller = FakeLocalCaller()

    async with _ASGIWebSocket(app, f"/connect?token={TEST_TOKEN}") as ws:
        # The connector pump: receive one relay request, forward it through the
        # real forward_one against the fake target, send the reply back.
        async def pump() -> None:
            raw = await ws.receive_text()
            reply = await forward_one(raw, connector_config, caller)
            await ws.send_text(reply)

        pump_task = asyncio.create_task(pump())

        # The phone posts a task against the real relay app, concurrently.
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://relay.test"
        ) as phone:
            resp = await phone.post(
                "/api/task",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
                json={"method": "POST", "path": "/run", "body": {"task": "deploy"}},
            )

        await asyncio.wait_for(pump_task, timeout=2.0)

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == 200
    # The body could ONLY have been produced by the fake local target, proving
    # the request really reached it through the connector.
    assert data["body"]["echo"] == {"task": "deploy"}

    # And the connector really called the configured Ronin target URL.
    assert len(caller.calls) == 1
    assert caller.calls[0]["url"] == "http://127.0.0.1:9999/ronin/run"


def test_task_requires_token(relay_config):
    app = create_app(relay_config)
    client = TestClient(app)
    # No Authorization header at all.
    resp = client.post("/api/task", json={"path": "/run"})
    assert resp.status_code == 401


def test_task_rejects_wrong_token(relay_config):
    app = create_app(relay_config)
    client = TestClient(app)
    resp = client.post(
        "/api/task",
        headers={"Authorization": "Bearer wrong-token"},
        json={"path": "/run"},
    )
    assert resp.status_code == 401


def test_task_alias_path_also_requires_token(relay_config):
    # The /task alias must enforce the same auth as the canonical /api/task.
    app = create_app(relay_config)
    client = TestClient(app)
    assert client.post("/task", json={"path": "/run"}).status_code == 401


def test_task_503_when_no_connector(relay_config):
    app = create_app(relay_config)
    client = TestClient(app)
    # Connector never attached: the laptop is offline.
    resp = client.post(
        "/api/task",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        json={"path": "/run", "body": {}},
    )
    assert resp.status_code == 503
    assert "offline" in resp.json()["error"]


def test_task_rate_limited(relay_config):
    # relay_config allows 5 per window; the sixth in-window request is 429.
    # All return 503 first (no connector) but the limiter runs before that
    # check, so once the budget is spent we see 429.
    app = create_app(relay_config)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
    codes = [
        client.post("/api/task", headers=headers, json={"path": "/run"}).status_code
        for _ in range(6)
    ]
    assert codes[:5] == [503, 503, 503, 503, 503]
    assert codes[5] == 429


def test_connector_rejects_bad_token(relay_config):
    app = create_app(relay_config)
    client = TestClient(app)
    # A connector presenting the wrong token must be refused the websocket.
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/connect?token=wrong") as ws:
            ws.receive_text()


def test_healthz_reports_connector_state(relay_config):
    app = create_app(relay_config)
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "connector_attached": False}
