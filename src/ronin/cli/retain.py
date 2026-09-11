"""The wire a Retainer is reached on: receive, authenticate, hand over.

``docs/RETAINER.md`` §8 step 6. This is the transport and nothing else. It knows
how to prove a delivery came from the service it claims to come from, and it
knows how to turn every possible failure into a response; what a delivery *means*
is the adapter's job, and what to do about it is the retainer plane's.

It lives in ``cli/`` for the reason ``http_api.py``, ``serve.py`` and ``acp.py``
do: it drives :class:`~ronin.cli.sdk.Agent`, which is the CLI's job and not the
core's. The layer graph forbids ``ronin.retainer`` from importing ``cli``, so the
seam is here rather than there.

**Stdlib, not a framework** — :class:`http.server.ThreadingHTTPServer`, ``hmac``
and ``json``. The tree ships zero hard dependencies and a web microframework to
answer one route would be the first.

**Authenticate before parsing.** :func:`route` verifies the signature over the
*raw bytes* and refuses before ``json.loads`` ever sees them. Parsing attacker-
controlled JSON and then deciding whether to trust it is the standard shape of
this bug: by then the parser has already run on hostile input, and any
side effect of parsing has already happened.

**No secret means no.** A receiver with nothing to verify against rejects every
delivery. The alternative — treating "unconfigured" as "unsigned is fine" — turns
a missing environment variable into an open endpoint, which is how a webhook
receiver gets found by somebody else first.

**Signing differs per service, so the scheme is data.** GitHub signs the body;
Slack signs a versioned string with a timestamp in it; Telegram sends a shared
token and signs nothing. Those are three :class:`SigningScheme` values rather
than three code paths, and the timestamp tolerance exists because an HMAC that
never expires is a replay waiting to be recorded.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import sys
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final

#: The one path a receiver answers. Deliberately one: a webhook endpoint is a
#: door, and every extra door is another thing to get the authentication right on.
DELIVERY_PATH: Final = "/retainer/deliver"

HEALTH_PATH: Final = "/retainer/health"

#: Webhook bodies are small. A receiver that will read whatever it is sent is a
#: memory exhaustion bug with a URL, so the cap is enforced before the read.
MAX_BODY_BYTES: Final = 1 << 20

#: How far a signed timestamp may be from now, in seconds, where the scheme has
#: one. Five minutes is what the services that bother with timestamps use; the
#: point is that a recorded delivery stops working, not the exact number.
DEFAULT_TOLERANCE_SECONDS: Final = 300.0


class DeliveryRefused(Exception):
    """The delivery was not accepted. Carries the status to answer with.

    One exception with a status rather than a hierarchy, because every caller
    does the same thing with it: turn it into a response and log the reason.
    """

    def __init__(self, status: HTTPStatus, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SigningScheme:
    """How one service proves a delivery is its own.

    ``prefix`` is the marker the service puts before the hex digest
    (``sha256=`` for GitHub, ``v0=`` for Slack). ``timestamp_header`` names the
    header folded into the signed string, and empty means the service does not
    sign a timestamp — in which case there is nothing to expire and the tolerance
    is not consulted.

    ``token_only`` is for a service that sends a shared secret in a header and
    computes no digest at all. It is still compared in constant time, because a
    string comparison that returns early leaks the secret one character per
    request to anybody willing to measure.
    """

    name: str
    signature_header: str
    prefix: str = ""
    timestamp_header: str = ""
    token_only: bool = False

    def signed_payload(self, raw_body: bytes, timestamp: str) -> bytes:
        """The bytes the digest is taken over."""
        if not self.timestamp_header:
            return raw_body
        return b"v0:" + timestamp.encode() + b":" + raw_body


#: GitHub signs the raw body with HMAC-SHA256 and no timestamp.
GITHUB_SCHEME: Final = SigningScheme(
    name="github", signature_header="X-Hub-Signature-256", prefix="sha256="
)

#: Slack signs ``v0:<timestamp>:<body>`` and sends the timestamp alongside.
SLACK_SCHEME: Final = SigningScheme(
    name="slack",
    signature_header="X-Slack-Signature",
    prefix="v0=",
    timestamp_header="X-Slack-Request-Timestamp",
)

#: Telegram sends back the secret token it was registered with. No digest.
TELEGRAM_SCHEME: Final = SigningScheme(
    name="telegram",
    signature_header="X-Telegram-Bot-Api-Secret-Token",
    token_only=True,
)

SCHEMES: Final[Mapping[str, SigningScheme]] = {
    scheme.name: scheme for scheme in (GITHUB_SCHEME, SLACK_SCHEME, TELEGRAM_SCHEME)
}


@dataclass(frozen=True, slots=True)
class Delivery:
    """One authenticated request, before anybody has decided what it means."""

    scheme: str
    headers: Mapping[str, str]
    body: Mapping[str, Any]
    raw_body: bytes = b""


@dataclass(frozen=True, slots=True)
class Response:
    """A status and a JSON body, decided before a byte is sent."""

    status: HTTPStatus
    body: dict[str, Any]


#: What a receiver does with an authenticated delivery. Injected, because
#: deciding is the retainer plane's job and this module may not know about it.
Receiver = Callable[[Delivery], Awaitable[Mapping[str, Any]]]


def header(headers: Mapping[str, str], name: str) -> str:
    """One header, case-insensitively. HTTP header names are not case-sensitive
    and a receiver that assumes the sender's capitalisation works until the
    sender changes it."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return ""


def verify(
    raw_body: bytes,
    headers: Mapping[str, str],
    *,
    scheme: SigningScheme,
    secret: str,
    now: float,
    tolerance: float = DEFAULT_TOLERANCE_SECONDS,
) -> None:
    """Prove this delivery came from ``scheme``'s service, or raise.

    Returns nothing on success on purpose: there is no truthy value a caller
    could accidentally ignore, and every failure is an exception carrying the
    status to answer with.
    """
    if not secret:
        raise DeliveryRefused(
            HTTPStatus.SERVICE_UNAVAILABLE,
            f"no {scheme.name} signing secret is configured, so no delivery can be "
            "verified and none is accepted",
        )
    offered = header(headers, scheme.signature_header)
    if not offered:
        raise DeliveryRefused(HTTPStatus.UNAUTHORIZED, f"missing {scheme.signature_header}")

    if scheme.token_only:
        if not hmac.compare_digest(offered, secret):
            raise DeliveryRefused(HTTPStatus.UNAUTHORIZED, "signature does not match")
        return

    timestamp = ""
    if scheme.timestamp_header:
        timestamp = header(headers, scheme.timestamp_header)
        if not timestamp:
            raise DeliveryRefused(HTTPStatus.UNAUTHORIZED, f"missing {scheme.timestamp_header}")
        try:
            sent = float(timestamp)
        except ValueError:
            raise DeliveryRefused(
                HTTPStatus.UNAUTHORIZED, f"{scheme.timestamp_header} is not a number"
            ) from None
        if abs(now - sent) > tolerance:
            raise DeliveryRefused(
                HTTPStatus.UNAUTHORIZED,
                f"timestamp is {abs(now - sent):.0f}s away from now, outside the "
                f"{tolerance:.0f}s window — a recorded delivery does not replay",
            )

    expected = (
        scheme.prefix
        + hmac.new(secret.encode(), scheme.signed_payload(raw_body, timestamp), sha256).hexdigest()
    )
    if not hmac.compare_digest(offered, expected):
        raise DeliveryRefused(HTTPStatus.UNAUTHORIZED, "signature does not match")


async def route(
    method: str,
    path: str,
    headers: Mapping[str, str],
    raw_body: bytes,
    *,
    scheme: SigningScheme,
    secret: str,
    receive: Receiver,
    now: Callable[[], float],
    tolerance: float = DEFAULT_TOLERANCE_SECONDS,
) -> Response:
    """The whole contract as one function. Socket-free and total.

    The order is the security property: method and path, then **signature**, then
    size, then parse. Nothing attacker-controlled is interpreted before it is
    proven to come from the service it claims to.
    """
    if path == HEALTH_PATH:
        if method != "GET":
            return _refusal(HTTPStatus.METHOD_NOT_ALLOWED, f"{path} takes GET")
        return Response(HTTPStatus.OK, {"ok": True, "scheme": scheme.name})
    if path != DELIVERY_PATH:
        return _refusal(HTTPStatus.NOT_FOUND, f"no route for {method} {path}")
    if method != "POST":
        return _refusal(HTTPStatus.METHOD_NOT_ALLOWED, f"{path} takes POST")

    try:
        verify(
            raw_body,
            headers,
            scheme=scheme,
            secret=secret,
            now=now(),
            tolerance=tolerance,
        )
    except DeliveryRefused as exc:
        return _refusal(exc.status, exc.reason)

    if len(raw_body) > MAX_BODY_BYTES:
        return _refusal(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            f"body is {len(raw_body)} bytes, over the {MAX_BODY_BYTES} byte limit",
        )
    try:
        parsed = json.loads(raw_body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _refusal(HTTPStatus.BAD_REQUEST, f"could not parse body as JSON: {exc}")
    if not isinstance(parsed, Mapping):
        return _refusal(HTTPStatus.BAD_REQUEST, "body must be a JSON object")

    delivery = Delivery(scheme=scheme.name, headers=dict(headers), body=parsed, raw_body=raw_body)
    try:
        result = await receive(delivery)
    except DeliveryRefused as exc:
        return _refusal(exc.status, exc.reason)
    except Exception as exc:  # the boundary: a receiver failure is a 500, never a crash
        return Response(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            {"ok": False, "error": f"receiver failed: {exc}"},
        )
    return Response(HTTPStatus.OK, {"ok": True, **dict(result)})


def _refusal(status: HTTPStatus, reason: str) -> Response:
    return Response(status, {"ok": False, "error": reason})


# --------------------------------------------------------------------------- #
# The thin HTTP layer
# --------------------------------------------------------------------------- #


class RetainerHTTPServer(ThreadingHTTPServer):
    """A receiver bound to a socket. Everything interesting is in :func:`route`."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        scheme: SigningScheme,
        secret: str,
        receive: Receiver,
        now: Callable[[], float],
        tolerance: float,
        log: Callable[[str], None] | None,
    ) -> None:
        super().__init__(address, handler)
        self.scheme = scheme
        self.secret = secret
        self.receive = receive
        self.now = now
        self.tolerance = tolerance
        self.log = log
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


class RetainerHTTPHandler(BaseHTTPRequestHandler):
    """Bytes in, bytes out. Every decision was already made by :func:`route`."""

    server_version = "ronin-retainer/1"

    @property
    def receiver(self) -> RetainerHTTPServer:
        server = self.server
        assert isinstance(server, RetainerHTTPServer)
        return server

    def _dispatch(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        # Read at most one byte over the cap: enough to know it was exceeded,
        # without letting the sender choose how much memory to spend.
        raw = self.rfile.read(min(length, MAX_BODY_BYTES + 1)) if length > 0 else b""
        receiver = self.receiver
        response = asyncio.run(
            route(
                method,
                self.path,
                dict(self.headers.items()),
                raw,
                scheme=receiver.scheme,
                secret=receiver.secret,
                receive=receiver.receive,
                now=receiver.now,
                tolerance=receiver.tolerance,
            )
        )
        payload = json.dumps(response.body).encode()
        self.send_response(int(response.status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_GET(self) -> None:
        self._dispatch("GET")

    def log_message(self, format: str, *args: Any) -> None:
        log = self.receiver.log
        if log is not None:
            log(format % args)


@dataclass(frozen=True, slots=True)
class ReceiverConfig:
    """Everything a bound receiver needs, so the signature does not grow forever."""

    scheme: SigningScheme
    secret: str
    receive: Receiver
    now: Callable[[], float] = field(default_factory=lambda: _monotonic_wall)
    tolerance: float = DEFAULT_TOLERANCE_SECONDS
    log: Callable[[str], None] | None = None


def _monotonic_wall() -> float:
    import time

    return time.time()


def _format_address(address: object) -> str:
    """``host:port`` for an IP socket, and something printable for anything else.

    ``server_address`` is a union across address families — a UNIX socket's is
    ``bytes`` — so formatting it blind produces ``b'/tmp/sock'`` in a log line.
    """
    if isinstance(address, tuple) and len(address) >= 2:
        host, port = address[0], address[1]
        text = host.decode(errors="replace") if isinstance(host, bytes) else str(host)
        return f"{text}:{port}"
    if isinstance(address, bytes):
        return address.decode(errors="replace")
    return str(address)


def build_server(address: tuple[str, int], config: ReceiverConfig) -> RetainerHTTPServer:
    """Bind a receiver without starting it. Port 0 picks a free one."""
    return RetainerHTTPServer(
        address,
        RetainerHTTPHandler,
        scheme=config.scheme,
        secret=config.secret,
        receive=config.receive,
        now=config.now,
        tolerance=config.tolerance,
        log=config.log,
    )


def serve_retainer(
    host: str,
    port: int,
    config: ReceiverConfig,
    *,
    err: Callable[[str], None] = lambda text: print(text, file=sys.stderr),
    serve: bool = True,
) -> RetainerHTTPServer:
    """Bind, announce, and start. ``serve=False`` binds without running, for tests."""
    server = build_server((host, port), config)
    shown = _format_address(server.server_address)
    err(f"retainer receiver listening on http://{shown}{DELIVERY_PATH} ({config.scheme.name})")
    if serve:
        server.start()
    return server


__all__ = [
    "DEFAULT_TOLERANCE_SECONDS",
    "DELIVERY_PATH",
    "GITHUB_SCHEME",
    "HEALTH_PATH",
    "MAX_BODY_BYTES",
    "SCHEMES",
    "SLACK_SCHEME",
    "TELEGRAM_SCHEME",
    "Delivery",
    "DeliveryRefused",
    "Receiver",
    "ReceiverConfig",
    "Response",
    "RetainerHTTPHandler",
    "RetainerHTTPServer",
    "SigningScheme",
    "build_server",
    "route",
    "serve_retainer",
    "verify",
]
