"""Watch a running session from somewhere that is not the terminal it started in.

A turn is an ``AsyncIterator[Event]`` with one consumer, and
:class:`~ronin.core.fanout.EventHub` is what makes that stream something several
watchers can hold at once. This is the wire over it: an HTTP endpoint that re-emits
the hub as **server-sent events**, so a browser tab, a phone or a second terminal can
follow a turn that is already running.

**Why SSE and not a websocket.** The stream is one-directional — this is watching, not
driving — and SSE is the protocol that already solves the two problems a watcher
actually has. Reconnection is built into every browser's ``EventSource``, and
``Last-Event-ID`` is sent automatically on reconnect, which is exactly the
``since=<seq>`` resumption the hub was built around: a watcher that loses its
connection in a tunnel comes back where it left off, and is told if the gap was bigger
than the ring. A websocket would need a framing library, a ping/pong policy and a
hand-written reconnect, in exchange for an upstream channel nothing here sends.

**Stdlib, like the rest of the tree.** :class:`http.server.ThreadingHTTPServer`, the
same one ``cli/http_api.py`` uses — this tree ships zero hard dependencies, and one
handler that writes text frames is not where that changes. Each connection is a thread
with no event loop, which is what :meth:`~ronin.core.fanout.Subscription.blocking`
exists for; the keep-alive is written on that iterator's timeout rather than from a
second thread per client.

**A token is not optional.** This stream carries the contents of a session: file
paths, source, diffs, the output of every command. That is a different exposure from
``cli/http_api.py``, which answers prompts a caller supplies. So there is no way to
serve it without a token — :func:`serve_watch` mints one when the caller does not
supply one, and a request without it gets ``401`` before a single event is written.
The comparison is :func:`hmac.compare_digest`, because a token checked with ``==``
leaks its prefix to anyone who can time the reply.

**Read-only, deliberately.** Nothing here accepts a prompt, an approval or a steer. A
watcher can see the session; it cannot touch it. Approval from a device that is not
the one the session trusts is a bigger decision than a transport, and putting it
behind the same token as "may read" would be answering it by accident.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.message import Message
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..core.fanout import Delivery, EventHub
from ..ui.headless import event_to_json

__all__ = [
    "KEEPALIVE_SECONDS",
    "RETRY_MS",
    "TOKEN_ENV",
    "WATCH_PATH",
    "WatchServer",
    "authorized",
    "bound_address",
    "build_watch_server",
    "headers_of",
    "mint_token",
    "serve_watch",
    "since_from",
    "sse_comment",
    "sse_frame",
]

#: The one route. A single path keeps the surface honest: there is nothing else to
#: reach, so there is nothing else to get wrong about who may reach it.
WATCH_PATH = "/events"

#: Where a token comes from when a wrapper script supplies one instead of reading the
#: minted one off the banner.
TOKEN_ENV = "RONIN_WATCH_TOKEN"

#: What an ``EventSource`` is told to wait before reconnecting. Sent once, as the
#: stream's first frame, so a dropped connection comes back quickly without the
#: browser's default back-off making a two-second blip look like a dead session.
RETRY_MS = 2000

#: How long a connection may be silent before a comment frame is written. Under any
#: proxy an idle stream is a stream that gets closed, and the watcher then reconnects
#: for no reason; the comment costs eight bytes and is ignored by every client.
KEEPALIVE_SECONDS = 15.0


def mint_token() -> str:
    """A fresh watch token. URL-safe, so it survives being pasted into a query string."""
    return secrets.token_urlsafe(32)


def sse_comment(text: str) -> str:
    """A comment frame — traffic that keeps a connection open and says nothing."""
    return f": {text}\n\n"


def sse_frame(delivery: Delivery) -> str:
    """One delivery as one SSE frame: its sequence number, its type, its JSON.

    ``id`` is the hub's sequence number, which is what comes back in the client's
    ``Last-Event-ID`` header on a reconnect — the two halves of resumption are the same
    number and neither side has to translate.

    ``missed`` rides inside the payload rather than being dropped or logged. A watcher
    that reconnects after the ring has rolled has a hole in its picture of the turn,
    and the only thing worse than telling it is not telling it: it would draw the gap
    as continuity.
    """
    payload = dict(event_to_json(delivery.event))
    payload["seq"] = delivery.seq
    if delivery.missed:
        payload["missed"] = delivery.missed
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=repr)
    # A `data:` line cannot contain a newline. `json.dumps` escapes them inside
    # strings, so this is belt-and-braces — but a frame split across raw lines is a
    # protocol error the client reports as a truncated event, which is a miserable
    # thing to debug from the other end of a phone.
    lines = "".join(f"data: {piece}\n" for piece in body.split("\n"))
    kind = str(payload.get("type", "message"))
    return f"id: {delivery.seq}\nevent: {kind}\n{lines}\n"


def headers_of(message: Message[str, str] | Mapping[str, str]) -> dict[str, str]:
    """Request headers as a plain dict, keyed lower-case.

    HTTP header names are case-insensitive and clients spell them however they like —
    ``EventSource`` sends ``Last-Event-ID``, a hand-rolled reconnect may well send
    ``last-event-id``. Normalizing once here is what lets the two functions below be
    pure and take an ordinary mapping, instead of each carrying its own opinion about
    capitalization and one of them being wrong.
    """
    return {str(key).lower(): str(value) for key, value in message.items()}


def since_from(headers: Mapping[str, str], query: Mapping[str, list[str]]) -> int | None:
    """Where this watcher wants to start, from ``Last-Event-ID`` or ``?since=``.

    The header wins, because a browser sets it by itself on a reconnect and a stale
    query string in the original URL would otherwise re-replay the whole ring every
    time the connection blinked.

    A value that is not a number is treated as "from the start of what is held" rather
    than refused. The header is written by a client library, not by a person, and a
    watcher that gets a ``400`` on reconnect because something mangled a header is a
    watcher that stays dark.
    """
    raw = headers.get("last-event-id") or _first(query.get("since"))
    if raw is None:
        return 0
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return 0


def _first(values: list[str] | None) -> str | None:
    return values[0] if values else None


def authorized(token: str, headers: Mapping[str, str], query: Mapping[str, list[str]]) -> bool:
    """Whether this request carries the watch token.

    Two spellings because two clients: ``Authorization: Bearer <token>`` for anything
    scripted, and ``?token=`` because the browser's ``EventSource`` cannot set a
    header. The query form is the weaker one — it lands in history and in any proxy's
    access log — which is a reason to keep watch sessions short-lived, not a reason to
    make the browser case impossible.

    An empty configured token authorizes nothing. A server that mints no token would
    otherwise serve every session to every caller, which is the failure this would be
    blamed for.
    """
    if not token:
        return False
    header = headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    offered = value.strip() if scheme.lower() == "bearer" else (_first(query.get("token")) or "")
    # `compare_digest` on both paths, and on a constant when nothing was offered, so
    # the reply time says nothing about how much of the token was right.
    return hmac.compare_digest(offered, token)


@dataclass(slots=True)
class WatchServer:
    """The bits a handler needs, kept off the handler class so it stays testable."""

    hub: EventHub
    token: str
    keepalive: float = KEEPALIVE_SECONDS
    log: Callable[[str], None] | None = None
    #: Live connections, for the banner and for a test that wants to know when a
    #: watcher has actually attached rather than guessing with a sleep.
    watchers: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def joined(self) -> int:
        with self._lock:
            self.watchers += 1
            return self.watchers

    def left(self) -> int:
        with self._lock:
            self.watchers -= 1
            return self.watchers

    def note(self, line: str) -> None:
        if self.log is not None:
            self.log(line)


class _WatchHTTPServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` carrying the :class:`WatchServer` its handlers read."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: Any, *, watch: WatchServer) -> None:
        self.watch = watch
        super().__init__(address, handler)


class WatchHandler(BaseHTTPRequestHandler):
    """``GET /events`` and nothing else. Every other path is a 404, every other verb 405."""

    protocol_version = "HTTP/1.1"
    #: Not a read timeout for the stream — the stream is meant to be long — but a cap
    #: on how long a client may take to send its request line and headers.
    timeout = 30

    def do_GET(self) -> None:
        split = urlsplit(self.path)
        if split.path != WATCH_PATH:
            self._refuse(
                HTTPStatus.NOT_FOUND, f"no route {split.path!r}; the stream is {WATCH_PATH}"
            )
            return
        watch = self._watch()
        query = parse_qs(split.query)
        headers = headers_of(self.headers)
        if not authorized(watch.token, headers, query):
            # No hint about whether the token was absent, short or wrong: each of
            # those is a fact about the token, and this endpoint answers questions
            # from anyone who can reach the port.
            self._refuse(HTTPStatus.UNAUTHORIZED, "a watch token is required")
            return
        self._stream(watch, since_from(headers, query))

    def do_POST(self) -> None:
        self._refuse(HTTPStatus.METHOD_NOT_ALLOWED, "the watch stream is read-only")

    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST

    def log_message(self, format: str, *args: Any) -> None:
        """Stdlib logs every request to stderr, which here is the session's own output."""
        self._watch().note((format % args) + "\n")

    def _watch(self) -> WatchServer:
        server = self.server
        assert isinstance(server, _WatchHTTPServer), "handler wired to the wrong server"
        return server.watch

    def _refuse(self, status: HTTPStatus, detail: str) -> None:
        payload = json.dumps({"error": detail}).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _stream(self, watch: WatchServer, since: int | None) -> None:
        self.send_response(int(HTTPStatus.OK))
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        # A proxy that buffers an event stream turns a live turn into one long pause
        # followed by everything at once, which is indistinguishable from a hang.
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        subscription = watch.hub.subscribe(since=since)
        watch.joined()
        try:
            self._write(f"retry: {RETRY_MS}\n\n")
            for delivery in subscription.blocking(timeout=watch.keepalive):
                if delivery is None:
                    self._write(sse_comment("keep-alive"))
                    continue
                self._write(sse_frame(delivery))
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            # The watcher went away. That is the normal end of a watch — a closed tab,
            # a phone that locked — and it must not reach the session as anything at
            # all. The hub has already forgotten the cursor; there is nothing to clean
            # up and nobody to tell.
            pass
        finally:
            watch.left()

    def _write(self, text: str) -> None:
        self.wfile.write(text.encode("utf-8"))
        self.wfile.flush()


def bound_address(address: object) -> str:
    """``host:port`` for the banner, whatever socket family the server bound."""
    if isinstance(address, tuple) and len(address) >= 2:
        host, port = address[0], address[1]
        shown = host.decode("utf-8", "replace") if isinstance(host, bytes) else str(host)
        return f"{shown}:{port}"
    return str(address)


def build_watch_server(
    address: tuple[str, int],
    *,
    hub: EventHub,
    token: str,
    keepalive: float = KEEPALIVE_SECONDS,
    log: Callable[[str], None] | None = None,
) -> _WatchHTTPServer:
    """Bind a watch server. Port ``0`` takes an ephemeral one off ``server_address``.

    An empty token is refused here rather than at the first request, because a server
    that binds and then rejects everything is a server whose operator believes it is
    working.
    """
    if not token:
        raise ValueError("a watch server needs a token; the stream carries session contents")
    watch = WatchServer(hub=hub, token=token, keepalive=keepalive, log=log)
    return _WatchHTTPServer(address, WatchHandler, watch=watch)


def serve_watch(
    hub: EventHub,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str = "",
    keepalive: float = KEEPALIVE_SECONDS,
    announce: Callable[[str], None] | None = None,
    serve: bool = True,
) -> _WatchHTTPServer:
    """Serve ``hub`` over SSE, announce the URL, and (by default) block on it.

    Loopback by default. Binding off-box is an explicit ``host`` from the operator,
    exactly as ``cli/http_api.py`` treats it — but unlike that endpoint there is no
    tokenless mode to fall back to, so an exposed port is a port that still needs the
    secret printed on the operator's own terminal.

    ``serve=False`` binds and returns without entering the loop, which is how a test
    drives the whole HTTP path over a real socket without needing to interrupt a
    blocking call.
    """
    secret = token or mint_token()
    server = build_watch_server((host, port), hub=hub, token=secret, keepalive=keepalive)
    if announce is not None:
        where = bound_address(server.server_address)
        announce(
            f"ronin watch: http://{where}{WATCH_PATH}?token={secret}\n"
            f"  read-only; the token is this session's and is not written to disk\n"
        )
    if serve:
        try:
            server.serve_forever()
        finally:
            server.server_close()
    return server
