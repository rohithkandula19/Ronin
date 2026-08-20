"""The HTTP client behind ``web_fetch``: vetted, pinned, and redirect-aware.

Until this module existed, :data:`~ronin.tools.net.Fetcher` was a type alias with no
implementation anywhere in the tree — ``web_fetch`` was assembled only by demos and
tests handing in a fake, and ``cli/wire.py`` never passed one, so a real session had no
``web_fetch`` at all. This is the missing client, and it is written around one problem.

**Checking a URL and then connecting to its hostname checks nothing.** The check asks
the resolver where a name points; the socket asks again. Between the two answers an
attacker who controls the name simply changes it — DNS rebinding, and the standard way
to defeat exactly the check :func:`ronin.safety.net.resolve_and_check` performs. So
this fetcher never hands a hostname to a socket. It resolves once, vets every address
it got back, connects to one of *those*, and keeps the hostname only where it belongs:
in the ``Host`` header and in the TLS handshake, so certificate verification still
happens against the name the user asked for rather than against an IP address.

**Redirects are followed here rather than by the library, on purpose.** A public URL
answering ``302 Location: http://169.254.169.254/`` is the same attack with one more
step, and it defeats any client that follows redirects internally — the check ran on
hop zero and the connection happened on hop one. :mod:`http.client` does not follow
redirects, which makes it the right tool: each hop comes back here, is re-vetted from
scratch, and counts against :data:`MAX_REDIRECTS`.

**Bounded, because a fetch is remote input.** A response is read to at most
:data:`MAX_RESPONSE_BYTES` and abandoned past that; the socket carries a timeout; the
redirect chain is finite. None of these are tuning knobs so much as the difference
between a tool and a way to hang an agent.

**No new dependency.** :mod:`http.client`, :mod:`socket` and :mod:`ssl` are standard
library, and they are what makes pinning possible at all: a higher-level client that
owns its own connection pool has nowhere to say "this name, that address". The cost is
that the request is blocking, so it runs in a worker thread — one thread per
``web_fetch``, which is the right shape for a tool that makes one request.

**Where this does not work: behind a proxy.** Pinning an address and routing through a
proxy are mutually exclusive — the proxy does its own name resolution, and there is no
address for us to pin. ``HTTPS_PROXY`` is therefore *ignored* rather than silently
half-honoured, and an environment that requires one should inject its own
:data:`~ronin.tools.net.Fetcher` through
:func:`~ronin.tools.registry.build_registry`. Said out loud because a fetcher that
quietly bypasses a corporate proxy is a surprise nobody wants to discover from a
firewall log.
"""

from __future__ import annotations

import asyncio
import http.client
import socket
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import Message as _Headers
from typing import Final, Protocol
from urllib.parse import urljoin

from ..safety.net import (
    Resolution,
    Resolver,
    UrlNotAllowed,
    redact_url,
    resolve_and_check,
    system_resolver,
)
from .base import ToolError
from .net import Fetcher

#: Seconds any single socket operation may take. A fetch is one request to somebody
#: else's server, and an agent waiting forever on it is an agent that has stopped.
DEFAULT_TIMEOUT_SECONDS: Final = 20.0

#: Bytes read from a response before it is abandoned. Past this the extractor would
#: truncate anyway (``MAX_MARKDOWN_CHARS``), so the rest is bandwidth spent to be
#: thrown away — and an unbounded read is how a fetch tool becomes a memory limit.
MAX_RESPONSE_BYTES: Final = 5_000_000

#: How many hops a redirect chain may take. Every hop is re-vetted, so this is not a
#: safety bound — it is the loop bound, for a server that redirects to itself.
MAX_REDIRECTS: Final = 5

#: Statuses that mean "look somewhere else". ``303`` included: it changes the method to
#: GET, which is what we were doing anyway.
REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})

#: Sent so a server answers with a page rather than with whatever it serves to a
#: client that claimed nothing. Honest about what it is: an agent, fetching for a user.
USER_AGENT: Final = "ronin/1.0 (+https://github.com/rohithkandula19/Ronin) web_fetch"


@dataclass(frozen=True, slots=True)
class Response:
    """One HTTP response, reduced to what a fetcher's caller needs."""

    status: int
    headers: _Headers
    body: bytes
    #: Set when the body hit :data:`MAX_RESPONSE_BYTES` and was cut short.
    truncated: bool = False

    def header(self, name: str, default: str = "") -> str:
        value = self.headers.get(name)
        return default if value is None else str(value)


class RawResponse(Protocol):
    """The three members of a response this module actually reads.

    Named structurally rather than as :class:`http.client.HTTPResponse` because that is
    all the coupling there is — and because ``http.client`` is on the banned-import list
    for this project's tests (they must not be able to open a socket), so a protocol
    mentioning it could not be satisfied by a fake in a test file at all. The real
    ``HTTPResponse`` satisfies this as it stands.
    """

    @property
    def status(self) -> int: ...

    @property
    def headers(self) -> _Headers: ...

    def read(self, amt: int | None = ...) -> bytes: ...


class Connection(Protocol):
    """The slice of :class:`http.client.HTTPConnection` this module uses.

    A protocol rather than the class itself so a test can drive the whole fetcher —
    policy, redirect chain, size cap, decoding — with no socket in sight, and can
    assert *which address* it was told to dial. That assertion is the point of the
    module, and it is not observable from the outside any other way.
    """

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = ...,
        headers: Mapping[str, str] = ...,
    ) -> None: ...

    def getresponse(self) -> RawResponse: ...

    def close(self) -> None: ...


#: Opens a connection to ``address`` while believing it is talking to ``host``. The
#: seam that makes pinning testable; the default is the real, pinned implementation.
Connector = Callable[..., Connection]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to a chosen address, with the certificate still checked against the name.

    The override is three lines and they are the whole security property. The base
    class resolves ``self.host`` in ``connect()``; this dials the vetted address
    instead and then wraps the socket with ``server_hostname=self.host``, so SNI and
    certificate verification both use the hostname. Getting only the first half right
    would connect safely to an attacker's address and then trust whatever certificate
    it presented.

    The ``SSLContext`` is kept on this instance rather than read back out of the base
    class's private ``_context``, so an internal rename in :mod:`http.client` cannot
    silently turn verification off.
    """

    def __init__(
        self,
        host: str,
        *,
        address: str,
        port: int | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        context: ssl.SSLContext | None = None,
    ) -> None:
        chosen = context or ssl.create_default_context()
        # Belt and braces: these are the defaults, and they are what the pinning is
        # for. A context handed in with verification off would make the whole exercise
        # decorative, so it is corrected rather than trusted.
        chosen.check_hostname = True
        chosen.verify_mode = ssl.CERT_REQUIRED
        super().__init__(host, port, timeout=timeout, context=chosen)
        self._address = address
        self._ssl_context = chosen

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._ssl_context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Plain HTTP to a chosen address, carrying the hostname in ``Host``.

    No TLS to get right, and nothing verified — which is true of ``http://`` generally
    and is why the policy in :mod:`ronin.safety.net` is what stands between this and a
    request to somebody's router.
    """

    def __init__(
        self,
        host: str,
        *,
        address: str,
        port: int | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


def default_connector(
    *,
    host: str,
    port: int | None,
    address: str,
    tls: bool,
    timeout: float,
) -> Connection:
    """The real pinned connection. Injected everywhere, so tests never reach it."""
    if tls:
        return _PinnedHTTPSConnection(host, address=address, port=port, timeout=timeout)
    return _PinnedHTTPConnection(host, address=address, port=port, timeout=timeout)


def _target(url: str) -> tuple[bool, str, int | None, str]:
    """``(tls, host, port, path)`` from a URL this module has already vetted.

    Hand-rolled for the same reason :func:`ronin.safety.net.split_host` is: the host is
    the *last* userinfo field, and a parser that disagrees with the checker about which
    part is the host is a parser that undoes the check.
    """
    scheme, _, remainder = url.partition("://")
    tls = scheme.lower() == "https"
    authority, slash, rest = remainder.partition("/")
    path = f"/{rest}" if slash else "/"
    authority = authority.rsplit("@", 1)[-1]
    port: int | None = None
    if authority.startswith("["):
        host, _, tail = authority.partition("]")
        host = host[1:]
        if tail.startswith(":") and tail[1:].isdigit():
            port = int(tail[1:])
    elif ":" in authority:
        host, _, text = authority.rpartition(":")
        port = int(text) if text.isdigit() else None
    else:
        host = authority
    return tls, host, port, path


def _charset(content_type: str) -> str:
    """The charset a response claims, or UTF-8. Never raises on a malformed header."""
    for part in content_type.split(";")[1:]:
        name, _, value = part.strip().partition("=")
        if name.strip().lower() == "charset" and value.strip():
            return value.strip().strip("\"'")
    return "utf-8"


def _decode(body: bytes, content_type: str) -> str:
    """Text from bytes, preferring the declared charset and never failing.

    ``errors="replace"`` because a page that lies about its encoding is a page we still
    want the model to read: a ``UnicodeDecodeError`` here would turn "this site is
    sloppy" into "this tool is broken".
    """
    try:
        return body.decode(_charset(content_type), errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def request_once(
    url: str,
    resolution: Resolution,
    *,
    connect: Connector,
    timeout: float,
    max_bytes: int,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
) -> Response:
    """One request, to a vetted address, with the hostname preserved.

    ``resolution`` is passed in rather than computed here so the caller — which is the
    thing that loops over redirects — cannot accidentally connect to a hop it has not
    vetted. The type makes the order the only possible one.

    ``method``, ``headers`` and ``body`` exist for :mod:`ronin.tools.searcher`, which
    talks to JSON APIs that want an auth header and sometimes a POST. They live here
    rather than in a second connection helper so there is exactly one place in the tree
    that decides what a pinned connection is — a searcher with its own socket code
    would be a searcher that eventually forgets to pin.
    """
    tls, host, port, path = _target(url)
    # An unpinnable resolution means the resolver could not answer and the policy let it
    # through, so the connection resolves the name itself. That is the one path in this
    # module with a rebinding window; it exists because refusing here would make a flaky
    # resolver look like a security failure. See `safety.net.Resolution`.
    address = resolution.addresses[0] if resolution.pinnable else host
    connection = connect(host=host, port=port, address=address, tls=tls, timeout=timeout)
    sent = {"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})}
    try:
        if body is None:
            connection.request(method, path, headers=sent)
        else:
            connection.request(method, path, body=body, headers=sent)
        raw = connection.getresponse()
        read = raw.read(max_bytes + 1)
        truncated = len(read) > max_bytes
        return Response(
            status=raw.status,
            headers=raw.headers,
            body=read[:max_bytes],
            truncated=truncated,
        )
    finally:
        connection.close()


def fetch_once(
    url: str,
    resolution: Resolution,
    *,
    connect: Connector,
    timeout: float,
    max_bytes: int,
) -> Response:
    """A plain ``GET`` through :func:`request_once`. Kept as the fetch path's own name."""
    return request_once(url, resolution, connect=connect, timeout=timeout, max_bytes=max_bytes)


def fetch_sync(
    url: str,
    *,
    resolve: Resolver = system_resolver,
    connect: Connector = default_connector,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = MAX_RESPONSE_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> tuple[str, str]:
    """``(content_type, text)`` for ``url``, vetting every hop. Blocking.

    Separated from :func:`pinned_fetcher` so the whole thing — policy, pinning, the
    redirect chain, the size cap — is testable as a plain function call, with no event
    loop and no thread.
    """
    current = url
    for hop in range(max_redirects + 1):
        try:
            resolution = resolve_and_check(current, resolve=resolve)
        except UrlNotAllowed as exc:
            # Converted at the boundary: `ToolError` is how this layer reports a refusal
            # the model should read and act on, rather than a crash.
            raise ToolError(str(exc)) from exc
        try:
            response = fetch_once(
                current,
                resolution,
                connect=connect,
                timeout=timeout,
                max_bytes=max_bytes,
            )
        except (OSError, http.client.HTTPException) as exc:
            raise ToolError(
                f"could not fetch {redact_url(current)!r}: {type(exc).__name__}: {exc}"
            ) from exc

        if response.status in REDIRECT_STATUSES:
            location = response.header("Location").strip()
            if not location:
                raise ToolError(
                    f"{redact_url(current)!r} answered {response.status} with no Location "
                    f"header, so there is nowhere to follow it to"
                )
            if hop == max_redirects:
                raise ToolError(
                    f"gave up after {max_redirects} redirect(s) starting at "
                    f"{redact_url(url)!r}; the last hop pointed at "
                    f"{redact_url(urljoin(current, location))!r}"
                )
            # Resolved against the current URL, so a relative Location works — and then
            # re-vetted from the top of the loop, which is the point of following
            # redirects by hand. `302 Location: http://169.254.169.254/` dies here.
            current = urljoin(current, location)
            continue

        if not 200 <= response.status < 300:
            raise ToolError(
                f"{redact_url(current)!r} answered HTTP {response.status}. The page was "
                f"not fetched; if this needs a login or a token, it cannot be read this "
                f"way."
            )
        content_type = response.header("Content-Type", "application/octet-stream")
        text = _decode(response.body, content_type)
        if response.truncated:
            text += f"\n\n[fetch truncated at {max_bytes:,} bytes]"
        return content_type, text

    # Unreachable: the loop either returns, raises, or exhausts `max_redirects` and
    # raises above. Kept as a named failure rather than an implicit `None`.
    raise ToolError(f"redirect handling for {redact_url(url)!r} ended without a response")


def pinned_fetcher(
    *,
    resolve: Resolver = system_resolver,
    connect: Connector = default_connector,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = MAX_RESPONSE_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> Fetcher:
    """A :data:`~ronin.tools.net.Fetcher` that vets, pins, and bounds every request.

    The blocking request runs in a worker thread so one ``web_fetch`` does not stall
    the event loop the agent is streaming on. Every seam is injectable, which is what
    lets the tests below exercise redirect chains and hostile addresses without a
    socket — and is what an environment behind a proxy uses to substitute its own
    client (see the module docstring).
    """

    async def fetch(url: str) -> tuple[str, str]:
        return await asyncio.to_thread(
            fetch_sync,
            url,
            resolve=resolve,
            connect=connect,
            timeout=timeout,
            max_bytes=max_bytes,
            max_redirects=max_redirects,
        )

    return fetch


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_REDIRECTS",
    "MAX_RESPONSE_BYTES",
    "REDIRECT_STATUSES",
    "USER_AGENT",
    "Connection",
    "Connector",
    "RawResponse",
    "Response",
    "default_connector",
    "fetch_once",
    "fetch_sync",
    "pinned_fetcher",
    "request_once",
]
