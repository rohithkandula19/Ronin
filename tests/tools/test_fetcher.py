"""The pinned HTTP client, driven with no socket and no DNS.

Every seam is injected, which is what lets this file assert the thing the module exists
for: **which address was dialled**. A fetcher that vets an address and then hands the
hostname to a socket passes every black-box test you can write — it fetches the right
pages and refuses the wrong URLs — and is still wide open to DNS rebinding, because the
check and the connection asked the resolver two separate questions. The only way to see
the difference is to look at what the connector was told, so that is what
:class:`Dialled` records and what most of these tests assert.

The other half is redirects. ``302 Location: http://169.254.169.254/`` is the same
attack with one more hop, and it defeats any client that follows redirects internally.
Here every hop comes back through the policy, and the tests check both that a hostile
hop is refused *and* that no connection to it was ever opened.

Offline by construction, and by rule: ``http.client`` is on this project's banned-import
list for tests, so the fakes below satisfy the module's ``RawResponse`` and
``Connection`` protocols structurally rather than by subclassing anything real.
"""

from __future__ import annotations

import email.message
import re
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

import pytest

from ronin.safety.net import Resolver, system_resolver
from ronin.tools.base import ToolError
from ronin.tools.fetcher import (
    MAX_REDIRECTS,
    REDIRECT_STATUSES,
    USER_AGENT,
    Response,
    _PinnedHTTPSConnection,
    default_connector,
    fetch_sync,
    pinned_fetcher,
)

PUBLIC: Final = "93.184.216.34"
OTHER_PUBLIC: Final = "151.101.1.1"

#: The names these tests resolve, and where to. Anything absent raises, which is what a
#: real resolver does for NXDOMAIN — the case the policy deliberately lets through.
ZONE: Final = {
    "good.example.com": (PUBLIC,),
    "hop.example.com": (OTHER_PUBLIC,),
    "evil.example.com": ("169.254.169.254",),
    "lan.example.com": ("192.168.1.1",),
}


def resolver(zone: dict[str, tuple[str, ...]] | None = None) -> Resolver:
    def resolve(host: str) -> tuple[str, ...]:
        table = ZONE if zone is None else zone
        if host not in table:
            raise OSError(f"Name or service not known: {host}")
        return table[host]

    return resolve


def headers(**fields: str) -> email.message.Message:
    """Response headers, built the way :mod:`http.client` hands them over."""
    message = email.message.Message()
    for name, value in fields.items():
        message[name.replace("_", "-")] = value
    return message


@dataclass
class FakeRaw:
    """A response, satisfying ``RawResponse`` without importing ``http.client``."""

    status: int
    headers: email.message.Message
    body: bytes

    def read(self, amt: int | None = None) -> bytes:
        return self.body if amt is None else self.body[:amt]


@dataclass
class Dialled:
    """What the connector was asked to do. The record these tests are written against."""

    host: str
    port: int | None
    address: str
    tls: bool
    path: str = ""
    user_agent: str = ""
    method: str = ""
    body: bytes | None = None


@dataclass
class FakeNetwork:
    """A scripted network: one response per connection, and a log of every dial."""

    script: list[FakeRaw]
    dialled: list[Dialled] = field(default_factory=list)
    closed: int = 0

    def connector(
        self, *, host: str, port: int | None, address: str, tls: bool, timeout: float
    ) -> FakeNetwork._Connection:
        record = Dialled(host=host, port=port, address=address, tls=tls)
        self.dialled.append(record)
        return FakeNetwork._Connection(self, record)

    @dataclass
    class _Connection:
        network: FakeNetwork
        record: Dialled

        def request(
            self,
            method: str,
            url: str,
            body: bytes | None = None,
            headers: Mapping[str, str] = MappingProxyType({}),
        ) -> None:
            self.record.method = method
            self.record.path = url
            self.record.body = body
            self.record.user_agent = headers.get("User-Agent", "")

        def getresponse(self) -> FakeRaw:
            if not self.network.script:
                raise AssertionError("the fetcher made more requests than the script allows")
            return self.network.script.pop(0)

        def close(self) -> None:
            self.network.closed += 1


def page(
    body: bytes = b"<h1>hello</h1>",
    *,
    status: int = 200,
    content_type: str = "text/html",
    **extra: str,
) -> FakeRaw:
    fields = {"Content_Type": content_type, **extra} if content_type else dict(extra)
    return FakeRaw(status=status, headers=headers(**fields), body=body)


def fetch(url: str, net: FakeNetwork, **kwargs: object) -> tuple[str, str]:
    return fetch_sync(url, resolve=resolver(), connect=net.connector, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# the property the module exists for
# --------------------------------------------------------------------------- #


def test_the_connection_goes_to_the_vetted_address_not_to_the_hostname() -> None:
    """The whole point. The address came from the policy's resolution; the *name* goes
    only into the ``Host`` header and the TLS handshake, which is what keeps certificate
    verification meaningful. A fetcher that dialled ``host`` here would look identical
    from the outside and be rebindable."""
    net = FakeNetwork([page()])
    content_type, text = fetch("https://good.example.com/docs/page", net)

    assert (content_type, text) == ("text/html", "<h1>hello</h1>")
    (dialled,) = net.dialled
    assert dialled.address == PUBLIC, "dialled the resolved address"
    assert dialled.host == "good.example.com", "and still believes it is talking to the name"
    assert dialled.tls is True
    assert dialled.path == "/docs/page"


def test_the_hostname_is_never_what_gets_dialled_even_once() -> None:
    """Stated as its own test because it is the regression that would be invisible: a
    refactor that passed ``host`` as the address would keep every other test green."""
    net = FakeNetwork([page()])
    fetch("https://good.example.com/", net)
    assert [d.address for d in net.dialled] == [PUBLIC]
    assert all(d.address != d.host for d in net.dialled)


def test_a_url_the_policy_refuses_opens_no_connection_at_all() -> None:
    """Refusing after connecting would already have leaked whatever the connection
    leaked. The check runs first, and the socket is never touched."""
    net = FakeNetwork([])
    with pytest.raises(ToolError, match="refusing to fetch"):
        fetch("https://evil.example.com/latest/meta-data/", net)
    assert net.dialled == []


def test_a_name_pointing_at_the_lan_is_refused_by_the_resolved_check() -> None:
    """``lan.example.com`` is a perfectly ordinary URL. Only resolution reveals it."""
    net = FakeNetwork([])
    with pytest.raises(ToolError, match=re.escape("192.168.1.1")):
        fetch("http://lan.example.com/admin", net)
    assert net.dialled == []


def test_a_port_and_a_plain_scheme_survive_into_the_connection() -> None:
    """The connector needs the real port and the real scheme, or a pinned fetch of
    ``http://host:8080/`` quietly becomes something else."""
    net = FakeNetwork([page(content_type="text/plain", body=b"ok")])
    fetch("http://good.example.com:8080/health", net)
    (dialled,) = net.dialled
    assert (dialled.tls, dialled.port, dialled.address) == (False, 8080, PUBLIC)


def test_userinfo_does_not_become_the_host() -> None:
    """The same trick the literal checker defends against, one layer down: the host is
    the last ``@``-separated field, and a connector that took the first would dial a
    name the policy never vetted."""
    net = FakeNetwork([page()])
    fetch("https://ignored@good.example.com/x", net)
    (dialled,) = net.dialled
    assert dialled.host == "good.example.com"
    assert dialled.address == PUBLIC


def test_every_request_says_who_it_is() -> None:
    """A server has a right to know it is talking to an agent."""
    net = FakeNetwork([page()])
    fetch("https://good.example.com/", net)
    assert net.dialled[0].user_agent == USER_AGENT
    assert "ronin" in USER_AGENT


def test_the_connection_is_closed_even_when_the_response_is_a_refusal() -> None:
    """A leaked socket per refused redirect is a leak per hostile page."""
    net = FakeNetwork([page(status=302, Location="http://169.254.169.254/")])
    with pytest.raises(ToolError):
        fetch("https://good.example.com/", net)
    assert net.closed == 1


# --------------------------------------------------------------------------- #
# redirects: the same attack, one hop later
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", sorted(REDIRECT_STATUSES))
def test_a_redirect_to_the_metadata_endpoint_is_refused_on_every_status(status: int) -> None:
    """Every redirect status, because a client that re-vets 302 and forwards 307 is a
    client with a hole in it. The assertion that matters is the second one: no
    connection to the metadata address was opened."""
    net = FakeNetwork([page(status=status, Location="http://169.254.169.254/latest/meta-data/")])
    with pytest.raises(ToolError, match="refusing to fetch"):
        fetch("https://good.example.com/start", net)
    assert [d.address for d in net.dialled] == [PUBLIC], "only the first, legitimate hop"


def test_a_redirect_to_a_public_host_is_followed_and_re_vetted() -> None:
    """Following redirects is not the problem; following them *unchecked* is. Each hop
    is resolved and vetted from scratch, and the second connection proves it went to the
    second name's own address rather than reusing the first."""
    net = FakeNetwork(
        [
            page(status=302, Location="https://hop.example.com/final"),
            page(body=b"landed", content_type="text/plain"),
        ]
    )
    assert fetch("https://good.example.com/start", net) == ("text/plain", "landed")
    assert [(d.host, d.address) for d in net.dialled] == [
        ("good.example.com", PUBLIC),
        ("hop.example.com", OTHER_PUBLIC),
    ]


def test_a_relative_redirect_is_resolved_against_the_current_url() -> None:
    """A ``Location: /elsewhere`` is legal and common. Mishandling it would either
    crash or, worse, be resolved against the *original* URL after a cross-host hop."""
    net = FakeNetwork(
        [
            page(status=302, Location="/second"),
            page(body=b"second", content_type="text/plain"),
        ]
    )
    assert fetch("https://good.example.com/first", net) == ("text/plain", "second")
    assert [d.path for d in net.dialled] == ["/first", "/second"]


def test_a_redirect_chain_is_bounded() -> None:
    """A server that redirects to itself must not become an infinite loop inside a tool
    call. Not a safety bound — every hop is checked — just a terminating one."""
    net = FakeNetwork([page(status=302, Location="https://good.example.com/again")] * 10)
    with pytest.raises(ToolError, match="gave up after 2 redirect"):
        fetch("https://good.example.com/start", net, max_redirects=2)
    assert len(net.dialled) == 3, "the first request plus two allowed hops"


def test_the_default_redirect_budget_is_small_and_stated() -> None:
    """Published as a constant because it is a behaviour a user can hit."""
    assert MAX_REDIRECTS == 5


def test_a_redirect_with_no_location_is_an_error_not_a_silent_page() -> None:
    """Returning the empty 302 body as "the page" would hand the model a blank page and
    call it success."""
    net = FakeNetwork([page(status=302, body=b"")])
    with pytest.raises(ToolError, match="no Location"):
        fetch("https://good.example.com/", net)


# --------------------------------------------------------------------------- #
# bounds and decoding
# --------------------------------------------------------------------------- #


def test_a_response_is_read_only_to_the_cap_and_says_it_was_cut() -> None:
    """An unbounded read of remote input is how a fetch tool becomes a memory limit.
    The marker matters as much as the cap: silently truncated text is text the model
    will reason about as though it were the whole page."""
    net = FakeNetwork([page(body=b"y" * 5_000, content_type="text/plain")])
    _content_type, text = fetch("https://good.example.com/big", net, max_bytes=100)
    assert text.startswith("y" * 100)
    assert "truncated at 100 bytes" in text
    assert len(text) < 300


def test_the_declared_charset_is_honoured() -> None:
    net = FakeNetwork(
        [page(body="café".encode("latin-1"), content_type="text/html; charset=latin-1")]
    )
    _content_type, text = fetch("https://good.example.com/", net)
    assert "café" in text


def test_a_page_that_lies_about_its_encoding_still_produces_text() -> None:
    """ "This site is sloppy" must not surface as "this tool is broken" — a
    ``UnicodeDecodeError`` here would fail the turn over somebody else's bad header."""
    net = FakeNetwork([page(body=b"\xff\xfe not utf-8", content_type="text/html; charset=utf-8")])
    _content_type, text = fetch("https://good.example.com/", net)
    assert "not utf-8" in text


def test_an_unknown_charset_falls_back_rather_than_raising() -> None:
    net = FakeNetwork([page(body=b"hello", content_type="text/html; charset=x-made-up")])
    assert "hello" in fetch("https://good.example.com/", net)[1]


def test_a_missing_content_type_gets_a_neutral_default() -> None:
    """``web_fetch`` switches on the content type to decide whether to reduce HTML, so
    a missing header must be a value rather than a crash."""
    net = FakeNetwork([FakeRaw(status=200, headers=headers(), body=b"raw")])
    content_type, text = fetch("https://good.example.com/", net)
    assert content_type == "application/octet-stream"
    assert text == "raw"


def test_a_failing_status_is_reported_as_a_refusal_with_the_number() -> None:
    """The model can act on "404" and cannot act on an empty page."""
    net = FakeNetwork([page(status=404, body=b"nope")])
    with pytest.raises(ToolError, match="404"):
        fetch("https://good.example.com/missing", net)


def test_a_socket_failure_becomes_a_tool_error_naming_what_happened() -> None:
    """``OSError`` from a real connection is expected, not exceptional: the tool reports
    it as a result the model reads, rather than as a crash in the turn."""

    def exploding(**_kwargs: object) -> object:
        raise OSError("Connection refused")

    with pytest.raises(ToolError, match="Connection refused"):
        fetch_sync("https://good.example.com/", resolve=resolver(), connect=exploding)  # type: ignore[arg-type]


def test_a_secret_in_the_url_is_redacted_in_a_transport_failure_too() -> None:
    """Every message this module produces goes through the redactor, not just the
    policy's own refusals."""

    def exploding(**_kwargs: object) -> object:
        raise OSError("Connection refused")

    with pytest.raises(ToolError) as caught:
        fetch_sync(
            "https://good.example.com/x?api_key=SUPERSECRET",
            resolve=resolver(),
            connect=exploding,  # type: ignore[arg-type]
        )
    assert "SUPERSECRET" not in str(caught.value)


# --------------------------------------------------------------------------- #
# the unresolvable case, which the policy allows through
# --------------------------------------------------------------------------- #


def test_an_unresolvable_name_is_dialled_by_name_since_there_is_nothing_to_pin() -> None:
    """The documented weak spot, pinned so it stays visible.

    The policy allows a name it could not resolve (see ``safety.net.Resolution``), and
    with no vetted address there is nothing to pin — so this one path hands the name to
    the connection and inherits a rebinding window. A name that genuinely does not
    resolve fails at connect, which is the only thing limiting it.
    """
    net = FakeNetwork([page()])
    fetch("https://unknown.example.com/", net)
    (dialled,) = net.dialled
    assert dialled.address == "unknown.example.com", "no address to pin, so the name is used"
    assert dialled.host == "unknown.example.com"


# --------------------------------------------------------------------------- #
# the async wrapper
# --------------------------------------------------------------------------- #


async def test_the_fetcher_satisfies_the_tool_layers_seam() -> None:
    """``pinned_fetcher`` returns something ``WebFetchTool`` can be handed: awaitable,
    ``(content_type, text)``, and doing its blocking work off the event loop."""
    net = FakeNetwork([page(body=b"async", content_type="text/plain")])
    fetch_it = pinned_fetcher(resolve=resolver(), connect=net.connector)
    assert await fetch_it("https://good.example.com/") == ("text/plain", "async")


async def test_a_refusal_survives_the_thread_boundary() -> None:
    """An exception raised in the worker thread has to arrive at the caller as itself,
    or the tool reports "failed unexpectedly" instead of the reason."""
    net = FakeNetwork([])
    fetch_it = pinned_fetcher(resolve=resolver(), connect=net.connector)
    with pytest.raises(ToolError, match="refusing to fetch"):
        await fetch_it("https://evil.example.com/")


async def test_the_whole_tool_works_on_top_of_it(tmp_path: object) -> None:
    """End to end through ``WebFetchTool``: a real fetcher, the real policy, the real
    injection fence, and a stub extractor standing in for the fast model.

    This is the integration the wiring in ``cli/wire.py`` makes real, so it is worth
    seeing once: HTML in, an extraction out, and the page fenced on the way to the
    model rather than pasted into the context.
    """
    from harness import context

    from ronin.tools.net import WebFetchTool

    net = FakeNetwork(
        [page(body=b"<html><body><h1>Changelog</h1><p>3.0 dropped sync.</p></body></html>")]
    )
    seen: list[str] = []

    async def extract(question: str, fenced: str) -> str:
        seen.append(fenced)
        return "3.0 dropped the sync client."

    tool = WebFetchTool(
        fetch=pinned_fetcher(resolve=resolver(), connect=net.connector),
        extract=extract,
        clock=lambda: 1000.0,
    )
    result = await tool.execute(
        {"url": "https://good.example.com/CHANGELOG", "prompt": "what changed in 3.0?"},
        context(tmp_path),  # type: ignore[arg-type]
    )

    assert result.ok, result.error
    assert "3.0 dropped the sync client." in result.content
    assert "# Changelog" in seen[0], "the page reached the model as reduced markdown"
    assert "untrusted" in seen[0].lower(), "and inside the injection fence"
    assert net.dialled[0].address == PUBLIC


async def test_the_tool_reports_a_blocked_url_as_a_result_not_a_crash(tmp_path: object) -> None:
    """``ToolResult(ok=False)`` across the boundary, which is this project's rule for
    every tool: the model reads the refusal and stops trying."""
    from harness import context

    from ronin.tools.net import WebFetchTool

    net = FakeNetwork([])
    tool = WebFetchTool(
        fetch=pinned_fetcher(resolve=resolver(), connect=net.connector),
        extract=lambda _question, _text: _never(),
        clock=lambda: 1000.0,
    )
    result = await tool.execute(
        {"url": "https://evil.example.com/latest/meta-data/", "prompt": "dump it"},
        context(tmp_path),  # type: ignore[arg-type]
    )

    assert not result.ok
    assert "refusing to fetch" in result.error
    assert net.dialled == []


async def _never() -> str:
    raise AssertionError("the extractor must not run for a refused URL")


# --------------------------------------------------------------------------- #
# the real connection classes, with the socket patched out
# --------------------------------------------------------------------------- #


def test_the_real_https_connection_dials_the_address_and_names_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The three lines the whole module rests on, exercised for real.

    ``connect()`` must dial the *address* and hand the *hostname* to
    ``wrap_socket(server_hostname=...)``. Getting only the first half right connects
    safely to an attacker's address and then trusts whatever certificate it presents;
    getting only the second right is a normal, rebindable client. Nothing here opens a
    socket — ``socket.create_connection`` and ``SSLContext.wrap_socket`` are patched, and
    what they were called with *is* the assertion.
    """
    dialled: list[tuple[object, float | None]] = []
    wrapped: list[str | None] = []

    def fake_create_connection(target: object, timeout: float | None = None) -> object:
        dialled.append((target, timeout))
        return "socket"

    def fake_wrap_socket(self: ssl.SSLContext, sock: object, **kwargs: object) -> object:
        wrapped.append(kwargs.get("server_hostname"))  # type: ignore[arg-type]
        return sock

    monkeypatch.setattr("ronin.tools.fetcher.socket.create_connection", fake_create_connection)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", fake_wrap_socket)

    connection = default_connector(
        host="good.example.com", port=None, address=PUBLIC, tls=True, timeout=7.0
    )
    connection.connect()  # type: ignore[attr-defined]

    assert dialled == [((PUBLIC, 443), 7.0)], "dialled the vetted address on the https port"
    assert wrapped == ["good.example.com"], "and verified the certificate against the name"


def test_the_real_http_connection_dials_the_address_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same property without TLS, and on the right default port."""
    dialled: list[object] = []

    def fake_create_connection(target: object, timeout: float | None = None) -> object:
        dialled.append(target)
        return "socket"

    monkeypatch.setattr("ronin.tools.fetcher.socket.create_connection", fake_create_connection)

    connection = default_connector(
        host="good.example.com", port=8080, address=PUBLIC, tls=False, timeout=1.0
    )
    connection.connect()  # type: ignore[attr-defined]

    assert dialled == [(PUBLIC, 8080)]


def test_certificate_verification_cannot_be_switched_off_by_the_caller() -> None:
    """A context arriving with verification disabled is corrected, not trusted.

    Pinning an address and then accepting any certificate would make the whole exercise
    decorative: an attacker who can answer on the vetted address is exactly who a
    certificate check is for.
    """
    unsafe = ssl.create_default_context()
    unsafe.check_hostname = False
    unsafe.verify_mode = ssl.CERT_NONE

    connection = _PinnedHTTPSConnection("good.example.com", address=PUBLIC, context=unsafe)

    assert connection._ssl_context.check_hostname is True
    assert connection._ssl_context.verify_mode is ssl.CERT_REQUIRED
    assert connection.host == "good.example.com", "the Host header keeps the name"


def test_constructing_a_connection_opens_nothing() -> None:
    """``http.client`` is lazy, which is what makes the tests above possible — and what
    means a refused URL costs no socket even in the real client."""
    connection = default_connector(
        host="good.example.com", port=None, address=PUBLIC, tls=True, timeout=1.0
    )
    assert connection.sock is None  # type: ignore[attr-defined]


def test_the_system_resolver_reads_every_address_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``getaddrinfo`` returns one entry per family *and* socket type, so the same
    address arrives several times; and both families have to be reported, because a
    name with a clean ``A`` record and a loopback ``AAAA`` record is the case a
    single-family check misses."""
    # Families and socket types as the plain ints `getaddrinfo` returns — spelled
    # literally because `socket` may not be imported in this project's tests at all.
    inet, inet6, stream, datagram = 2, 10, 1, 2
    entries = [
        (inet, stream, 6, "", ("93.184.216.34", 80)),
        (inet, datagram, 17, "", ("93.184.216.34", 80)),
        (inet6, stream, 6, "", ("::1", 80, 0, 0)),
    ]
    monkeypatch.setattr("ronin.safety.net.socket.getaddrinfo", lambda *_a, **_k: entries)

    assert system_resolver("dual.example.com") == ("93.184.216.34", "::1")


def test_a_hostname_the_encoder_rejects_is_an_oserror_like_any_other_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``getaddrinfo`` raises ``UnicodeError`` — not ``OSError`` — for a name IDNA
    cannot encode. Left alone it would escape the policy's ``except OSError`` and crash
    a turn instead of being treated as "could not resolve"."""

    def exploding(*_args: object, **_kwargs: object) -> object:
        raise UnicodeError("label empty or too long")

    monkeypatch.setattr("ronin.safety.net.socket.getaddrinfo", exploding)

    with pytest.raises(OSError, match="cannot encode the hostname"):
        system_resolver("x" * 100 + ".example.com")


def test_the_response_value_reads_headers_case_insensitively() -> None:
    """HTTP header names are case-insensitive and servers exercise that freedom."""
    response = Response(status=200, headers=headers(Content_Type="text/plain"), body=b"")
    assert response.header("content-type") == "text/plain"
    assert response.header("CONTENT-TYPE") == "text/plain"
    assert response.header("missing", "fallback") == "fallback"
