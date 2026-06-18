"""The relay server (runs on the free VM).

Responsibilities:
  1. Accept ONE laptop connector over a persistent outbound websocket
     (/connect). The connector authenticates with the shared token.
  2. Accept phone task requests over HTTP (POST /api/task). The phone
     authenticates with the same shared token.
  3. For each phone request: assign an id, push a RelayRequest down the
     connector websocket, wait for the matching ConnectorReply, return it.

The websocket is registered at both /connect (canonical) and /connector (an
alias kept for older connectors), and the task endpoint at both /api/task
(canonical) and /task (alias). Both names behave identically.

The relay holds NO secrets about the laptop's network and opens NO connection
to the laptop. The laptop dialed out to us; we only answer on the socket it
opened. If no connector is attached, phone requests get 503 (laptop offline).

State is in memory only. Restarting the relay drops the session, and the
connector reconnects. That is fine for a single small box and keeps us free of
any database.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from .config import RelayConfig
from .webui import PAGE_HTML
from .protocol import (
    KIND_REPLY,
    ConnectorReply,
    RelayRequest,
    TaskRequest,
    encode,
)
from .security import RateLimiter, extract_bearer, token_matches

logger = logging.getLogger("ronin_relay")


def log_event(event: str, **fields: Any) -> None:
    """Emit one structured (JSON) log line.

    Structured logs keep this security-sensitive service auditable: each line is
    a single JSON object with an event name and machine-readable fields, so a
    log shipper or plain grep can find every relayed request, every auth
    failure, and every timeout without parsing free text. The token is never a
    field, so it cannot leak into the logs.
    """
    record = {"event": event, "ts": round(time.time(), 3)}
    record.update(fields)
    logger.info(json.dumps(record, separators=(",", ":"), sort_keys=True))


class ConnectorSession:
    """Tracks the single attached connector and its pending requests.

    pending maps request id -> Future that the HTTP handler is awaiting. When a
    reply arrives on the websocket, we resolve the matching future.
    """

    def __init__(self) -> None:
        self.websocket: WebSocket | None = None
        self.pending: dict[str, asyncio.Future[ConnectorReply]] = {}
        self._send_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self.websocket is not None

    async def send_request(self, message: RelayRequest) -> None:
        ws = self.websocket
        if ws is None:
            raise RuntimeError("no connector attached")
        # Serialize sends so two phone requests cannot interleave frames.
        async with self._send_lock:
            await ws.send_text(encode(message))

    def attach(self, ws: WebSocket) -> None:
        self.websocket = ws

    def detach(self, ws: WebSocket) -> None:
        # Only clear if it is still the same socket (avoid races on reconnect).
        if self.websocket is ws:
            self.websocket = None
        # Fail any in-flight requests so phones do not hang forever.
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("connector disconnected"))
        self.pending.clear()


def create_app(config: RelayConfig) -> FastAPI:
    """Build the relay FastAPI app bound to the given config."""
    app = FastAPI(title="ronin-relay", version="0.1.0")
    session = ConnectorSession()
    limiter = RateLimiter(config.rate_limit_max, config.rate_limit_window_seconds)

    # Expose internals for tests and health reporting.
    app.state.session = session
    app.state.config = config

    @app.get("/")
    async def index() -> HTMLResponse:
        # Serve the mobile web UI. This page carries NO secret: it asks the user
        # for the token in the browser and stores it only on the device, so it
        # is safe to serve unauthenticated. Every actual task still requires the
        # token on POST /api/task. The page loads no external resources, so a
        # strict content security policy that forbids any remote origin is both
        # correct and a defense against an injected third-party script.
        headers = {
            "Content-Security-Policy": (
                "default-src 'none'; "
                "style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; "
                "connect-src 'self'; "
                "base-uri 'none'; "
                "form-action 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        }
        return HTMLResponse(content=PAGE_HTML, headers=headers)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        # No auth on health: it reveals only whether a laptop is currently
        # attached, which is not sensitive, and lets a free-tier uptime pinger
        # keep the box warm.
        return {"ok": True, "connector_attached": session.connected}

    async def connector_endpoint(ws: WebSocket) -> None:
        # Authenticate BEFORE accepting. The token may arrive as a query param
        # (websocket clients cannot always set headers) or an auth header.
        presented = ws.query_params.get("token") or extract_bearer(
            ws.headers.get("authorization")
        )
        if not token_matches(presented, config.token):
            await ws.close(code=4401)
            log_event("connector_rejected", reason="bad_or_missing_token")
            return
        await ws.accept()
        # Single-connector model: a new connector replaces the old one.
        session.attach(ws)
        log_event("connector_attached")
        try:
            while True:
                raw = await ws.receive_text()
                reply = ConnectorReply.model_validate_json(raw)
                if reply.kind != KIND_REPLY:
                    continue
                fut = session.pending.pop(reply.id, None)
                if fut is not None and not fut.done():
                    fut.set_result(reply)
        except WebSocketDisconnect:
            log_event("connector_disconnected")
        finally:
            session.detach(ws)

    # Canonical path is /connect (task spec); /connector is a kept alias.
    app.add_api_websocket_route("/connect", connector_endpoint)
    app.add_api_websocket_route("/connector", connector_endpoint)

    async def task_endpoint(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        presented = extract_bearer(authorization)
        if not token_matches(presented, config.token):
            log_event("task_unauthorized")
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        if not limiter.allow(presented):
            log_event("task_rate_limited")
            return JSONResponse(
                {"error": "rate limited"}, status_code=429
            )

        if not session.connected:
            # The laptop is not attached: it is off, asleep, or the connector
            # is not running. We honestly cannot reach it.
            log_event("task_no_connector")
            return JSONResponse(
                {"error": "laptop offline: no connector attached"},
                status_code=503,
            )

        payload = await request.json()
        task = TaskRequest.model_validate(payload)
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ConnectorReply] = loop.create_future()
        session.pending[request_id] = fut
        log_event(
            "task_received", request_id=request_id, method=task.method, path=task.path
        )

        try:
            await session.send_request(
                RelayRequest(id=request_id, request=task)
            )
            reply = await asyncio.wait_for(
                fut, timeout=config.request_timeout_seconds
            )
        except asyncio.TimeoutError:
            session.pending.pop(request_id, None)
            log_event("task_timeout", request_id=request_id)
            return JSONResponse(
                {"error": "timeout waiting for laptop"}, status_code=504
            )
        except RuntimeError as exc:
            session.pending.pop(request_id, None)
            log_event("task_connector_lost", request_id=request_id, detail=str(exc))
            return JSONResponse({"error": str(exc)}, status_code=502)

        if reply.error:
            log_event(
                "task_target_unreachable", request_id=request_id, detail=reply.error
            )
            return JSONResponse(
                {"error": "target unreachable", "detail": reply.error},
                status_code=502,
            )

        log_event("task_relayed", request_id=request_id, target_status=reply.status)
        return JSONResponse(
            {"status": reply.status, "body": reply.body},
            status_code=200,
        )

    # Canonical path is /api/task (task spec); /task is a kept alias.
    app.add_api_route("/api/task", task_endpoint, methods=["POST"])
    app.add_api_route("/task", task_endpoint, methods=["POST"])

    return app
