"""Where a fetch may go, and what a URL is allowed to say in a log.

Two jobs, both about the same string.

**Where a fetch may go.** A URL the *model* chose is a request this machine makes
from inside the network the user trusts, which is the shape of every SSRF. The
target that matters is not a secret host: it is ``http://169.254.169.254/``, the
cloud metadata endpoint, which answers unauthenticated HTTP with the instance's
role credentials. A fetch tool without this check turns "summarize this page" into
credential exfiltration, and the model does not have to be malicious for it to
happen — a fetched page that *asks* for it is enough, which is exactly the case
:mod:`~ronin.safety.injection` exists for.

The check before this one was a literal comparison against five spellings of
localhost. Every address in the table below reached the fetcher:

===============================  ==========================================
``169.254.169.254``              link-local — the metadata endpoint itself
``10.0.0.5`` ``192.168.1.1``     private ranges: the user's own network
``127.1`` ``2130706433``         loopback in the notations ``curl`` accepts
``[::ffff:127.0.0.1]``           loopback wearing IPv6
``metadata.google.internal``     an internal *name*, no literal address at all
===============================  ==========================================

So: classify with :mod:`ipaddress` rather than by string, and parse the legacy
numeric notations ourselves rather than trusting ``inet_aton``, whose acceptance of
octal and hex differs between platforms — a check that blocks on Linux and passes on
macOS is not a check, and this project's CI runs both.

**Fail closed on anything unclassifiable.** A host that is neither a legal DNS name
nor an address we can parse is refused. The alternative — pass it through and hope
the resolver agrees with us — is how a bypass gets written as a one-character
notation nobody thought of.

**What a URL may say in a log.** Secrets travel in query strings (``?token=…``,
a presigned S3 signature, an OAuth ``code``) and in userinfo (``https://user:pw@``).
:func:`redact_url` keeps the scheme, host and path and replaces *every* query value
and any fragment, rather than the values whose names look secret-ish. The broad rule
is the same argument as the one in :func:`ronin.ui.render.strip_controls`: "no query
value appears in a log" is one sentence that can be tested exhaustively, while "no
*secret* query value" is a list of parameter names that has to be maintained against
every API anyone integrates, and a missed name is silent. The cost is that
``?page=2`` is redacted too; parameter *names* stay visible, so the shape of a URL
survives for debugging.

**Two checks, because a URL has two truths.** :func:`check_url` judges the URL as
written; :func:`resolve_and_check` also judges what the hostname *resolves to*, and
returns those addresses so the caller can connect to them instead of to the name. The
second is not optional politeness: publishing an ``A`` record that points at
``169.254.169.254`` is the ordinary way to reach a metadata endpoint, and such a URL
passes every literal test here because nothing about it is malformed.

Pinning is what makes the resolved check real rather than advisory. A fetcher that
vets an address and then hands the *name* to a socket has checked one DNS answer and
connected on another — the rebinding window. :class:`Resolution` therefore carries
the vetted addresses, and :func:`ronin.tools.fetcher.pinned_fetcher` dials them while
keeping the hostname in the ``Host`` header and the TLS handshake.

Known gaps, named rather than papered over:

* **A resolver that cannot answer is allowed through**, not refused — a deliberate
  choice, and the weaker of the two available ones. Argued in :class:`Resolution`.
* **6to4 and Teredo** are blocked by prefix, not by unwrapping the IPv4 address
  embedded inside them.
* **Resolution is not cached and not shared with the connection's own lookup.** The
  pinned fetcher closes that by connecting to what was vetted, but anything else
  built on :func:`resolve_and_check` alone inherits the rebinding window.

Depends on :mod:`ipaddress` and :mod:`re` from the standard library, and nothing
else. No new dependency: the classification is a property lookup on a stdlib type.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

#: Turns a hostname into the addresses it points at. Injected everywhere it is used:
#: DNS is the one thing in this module that touches the network, and a test suite that
#: is required to be offline cannot be allowed to depend on what a real resolver says
#: about a real name today.
Resolver = Callable[[str], Sequence[str]]

#: Schemes a fetch may use. ``file://`` is a read, not a fetch, and ``gopher://``
#: and friends are request-smuggling primitives in a URL parser's clothing.
ALLOWED_SCHEMES: Final = ("http://", "https://")

#: Ranges :mod:`ipaddress` does not call private but which are not the public
#: internet either. Checked in addition to the property lookups in
#: :func:`address_reason`, because ``100.64.0.1`` reports ``is_private=False``:
#: carrier-grade NAT is shared address space, and on a home connection behind it
#: the neighbours are reachable.
EXTRA_BLOCKED: Final = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "100.64.0.0/10",  # RFC 6598 carrier-grade NAT
        "192.0.0.0/24",  # RFC 6890 IETF protocol assignments
        "192.0.2.0/24",  # RFC 5737 documentation ranges — never a real host
        "198.51.100.0/24",
        "203.0.113.0/24",
        "2002::/16",  # 6to4: an IPv4 address wearing IPv6
        "2001::/32",  # Teredo, same
    )
)

#: Hostname suffixes that mean "inside", by convention rather than by address.
#: ``.internal`` is what GCP's metadata server answers to, and mDNS ``.local``
#: names resolve on the user's LAN.
BLOCKED_SUFFIXES: Final = (".internal", ".local", ".localhost", ".home.arpa")

#: Bare names that are loopback by definition.
BLOCKED_NAMES: Final = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})

#: One DNS label: letters, digits, hyphen, not starting or ending with a hyphen.
_LABEL: Final = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)\Z")

#: Query parameter and fragment values are replaced with this, so a redacted URL
#: reads as deliberately redacted rather than as an empty value.
REDACTED: Final = "<redacted>"


class UrlNotAllowed(ValueError):
    """A URL this program refuses to fetch.

    ``ValueError`` because the URL is bad input, not a failure of the machine. The
    message is written for the model that supplied it — it says which property was
    disqualifying and what to do instead, since a refusal the model cannot act on
    just becomes a retry of the same call.
    """


def _numeric_address(host: str) -> ipaddress.IPv4Address | None:
    """The legacy IPv4 notations, parsed here rather than by the platform.

    ``curl http://2130706433/`` and ``curl http://0x7f.1/`` both reach 127.0.0.1:
    ``inet_aton`` accepts one to four parts, each decimal, octal (leading zero) or
    hex (leading ``0x``), with the final part filling all remaining octets. Python's
    :func:`ipaddress.ip_address` accepts none of it, which is why a check built only
    on that function passes ``http://2130706433/`` straight through.

    Implemented rather than delegated to :func:`socket.inet_aton` because glibc,
    musl and macOS disagree about which of these forms they accept, and a check whose
    verdict depends on the host OS is a check that fails open on one of them.
    """
    parts = host.split(".")
    if len(parts) > 4 or not all(parts):
        return None
    values: list[int] = []
    for part in parts:
        try:
            if part.lower().startswith(("0x", "-0x")):
                values.append(int(part, 16))
            elif part.startswith("0") and part != "0":
                values.append(int(part, 8))
            else:
                values.append(int(part, 10))
        except ValueError:
            return None
    if any(value < 0 for value in values):
        return None
    # The last part fills every octet the earlier parts did not name: in `10.1`,
    # `1` is the low *three* octets, so the address is 10.0.0.1.
    packed = 0
    for value in values[:-1]:
        if value > 0xFF:
            return None
        packed = (packed << 8) | value
    tail_octets = 4 - (len(values) - 1)
    if values[-1] >= 1 << (8 * tail_octets):
        return None
    packed = (packed << (8 * tail_octets)) | values[-1]
    try:
        return ipaddress.IPv4Address(packed)
    except ipaddress.AddressValueError:  # pragma: no cover - packed is in range by construction
        return None


def parse_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """``host`` as an IP address, in any notation, or ``None`` if it is a name.

    IPv6 arrives bracketed in a URL (``http://[::1]/``); the brackets are the URL's
    syntax, not part of the address, and are stripped before parsing. A mapped
    address (``::ffff:127.0.0.1``) is unwrapped to the IPv4 address it carries so
    that one loopback check covers both spellings.
    """
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return _numeric_address(candidate)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def address_reason(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """Why this address is not a fetch target, or ``""`` if it is fine.

    Property lookups on the stdlib type rather than a table of CIDRs, so the ranges
    stay correct as :mod:`ipaddress` tracks the RFCs — with :data:`EXTRA_BLOCKED` for
    the ones it does not classify the way this check needs.
    """
    if address.is_unspecified:
        return "the unspecified address (0.0.0.0 / ::), which routes to this machine"
    if address.is_loopback:
        return "a loopback address — this machine"
    if address.is_link_local:
        return "a link-local address, which is where cloud metadata endpoints live"
    if address.is_multicast:
        return "a multicast address, not a host"
    if address.is_private:
        return "a private address, inside the network this machine sits in"
    if address.is_reserved:
        return "a reserved address"
    if any(address in network for network in EXTRA_BLOCKED):
        return "shared or reserved address space, not a public host"
    return ""


def host_reason(host: str) -> str:
    """Why this host is not a fetch target, or ``""`` if it is fine.

    Addresses are classified numerically; names are checked against the suffixes
    that mean "inside" and then validated as DNS names. The validation is the
    fail-closed half: a host that is neither a name nor an address we can parse is
    refused rather than handed to a resolver whose interpretation we do not know.
    """
    if not host:
        return "no host at all"
    lowered = host.lower()
    address = parse_address(lowered)
    if address is not None:
        return address_reason(address)
    if lowered in BLOCKED_NAMES:
        return "a loopback name — this machine"
    if lowered.endswith(BLOCKED_SUFFIXES):
        return "an internal hostname, which resolves inside the network, not on the internet"
    name = lowered.removesuffix(".")
    if not name or len(name) > 253 or not all(_LABEL.match(label) for label in name.split(".")):
        return "not a hostname this program can classify, so it is refused rather than guessed at"
    if name.rsplit(".", 1)[-1].isdigit():
        # `999.1.1.1` and `1.2.3.4.5` are not addresses — `parse_address` rejected
        # both — but every label is legal in a hostname, so without this they arrive
        # here classified as *names* and pass. A top-level label cannot be all digits
        # (RFC 3696), so anything ending in one is a malformed address rather than a
        # host, and a malformed address is exactly the kind of input this module
        # refuses instead of handing to a resolver to interpret. Found by the table in
        # `tests/safety/test_safety_net.py`, which is the argument for writing the
        # blocked cases out as data rather than picking a few examples.
        return "a malformed numeric address — no top-level domain is all digits"
    return ""


def split_host(url: str) -> str:
    """The host out of a URL, with userinfo and port removed.

    Hand-rolled rather than :func:`urllib.parse.urlsplit` because the userinfo rule
    is the whole point: in ``http://expected.example.com@169.254.169.254/`` the host
    is the *last* ``@``-separated field, and a check that reads the first one is
    reading the attacker's decoration. The port is discarded — a blocked address is
    blocked on every port.
    """
    remainder = url.split("://", 1)[1] if "://" in url else url
    authority = re.split(r"[/?#]", remainder, maxsplit=1)[0]
    host = authority.rsplit("@", 1)[-1]
    if host.startswith("["):
        return host[: host.index("]") + 1] if "]" in host else host
    return host.rsplit(":", 1)[0] if ":" in host else host


def redact_url(url: str) -> str:
    """A URL safe to write down: no userinfo, no query values, no fragment.

    Parameter names survive, so ``?token=abc&page=2`` becomes
    ``?token=<redacted>&page=<redacted>`` — enough to see what was called with what
    shape, and nothing to lift out of a log file.

    Note where this is *not* applied: the session transcript records tool arguments
    verbatim, because it is the replay source and a redacted argument would make a
    session unreplayable. ``.ronin/sessions`` is therefore as sensitive as the work
    done in it, which is why it lives in the workspace rather than anywhere shared.
    """
    stripped = url.strip()
    head, _, fragment = stripped.partition("#")
    base, _, query = head.partition("?")
    if "://" in base:
        scheme, _, rest = base.partition("://")
        base = f"{scheme}://{rest.rsplit('@', 1)[-1]}" if "@" in rest.split("/", 1)[0] else base
    if query:
        pairs = []
        for field in query.split("&"):
            if not field:
                continue
            name, sep, _ = field.partition("=")
            pairs.append(f"{name}={REDACTED}" if sep else f"{name}")
        base = f"{base}?{'&'.join(pairs)}" if pairs else base
    return f"{base}#{REDACTED}" if fragment else base


def check_url(url: str) -> str:
    """``url`` if it may be fetched, else raise :exc:`UrlNotAllowed`.

    The returned string is the stripped original: this function decides, it does not
    rewrite. Every message quotes the URL through :func:`redact_url`, because a
    refusal is text that gets shown, copied into an issue and written to a log, and a
    secret in a rejected URL is still a secret.
    """
    stripped = url.strip()
    if not stripped.lower().startswith(ALLOWED_SCHEMES):
        raise UrlNotAllowed(
            f"{redact_url(stripped)!r} is not an http(s) URL. Only http and https are "
            "fetched — a file:// path should be read with the read tool."
        )
    reason = host_reason(split_host(stripped))
    if reason:
        raise UrlNotAllowed(
            f"refusing to fetch {redact_url(stripped)!r}: it points at {reason}. If you "
            "meant a local or internal service, run the request through bash so it is "
            "explicit and gets reviewed on its own terms."
        )
    return stripped


def system_resolver(host: str) -> tuple[str, ...]:
    """Every address ``host`` currently resolves to, via the system resolver.

    The only function in this package that touches the network, which is why it is a
    named default rather than an inline call: everything that resolves takes a
    :data:`Resolver`, so every test injects one and the suite stays offline.

    ``AF_UNSPEC`` on purpose — both families, because a name with a harmless ``A``
    record and a loopback ``AAAA`` record is a name that reaches this machine on any
    client that prefers IPv6, which is most of them. Deduplicated with order kept so
    an error message reads the way the resolver answered.
    """
    try:
        found = socket.getaddrinfo(host, 80, proto=socket.IPPROTO_TCP)
    except UnicodeError as exc:  # an IDNA name the encoder rejects
        raise OSError(f"cannot encode the hostname {host!r}: {exc}") from exc
    return tuple(dict.fromkeys(str(entry[4][0]) for entry in found))


@dataclass(frozen=True, slots=True)
class Resolution:
    """Where a hostname actually points, as far as we were able to find out.

    ``addresses`` is what a fetcher should **pin**: connecting to a name it resolves
    again is how a check gets bypassed between the check and the connection, since the
    second answer need not match the first (DNS rebinding). A fetcher that dials
    ``addresses`` and carries ``host`` in the ``Host`` header and TLS SNI is doing the
    only version of this that is not advisory.

    ``resolved`` is ``False`` when the resolver could not answer — and the URL is then
    **allowed** through, which is a deliberate choice with a real cost:

        A resolver that fails open is a resolver an attacker can arrange to fail.
        Someone who can make resolution fail for a name they control gets the same
        pass as someone whose name simply does not exist.

    The reason for accepting that: refusing would make every fetch on a machine with a
    flaky or absent resolver a security refusal, which reads as a bug and teaches
    people to disable the check. The mitigation is that ``addresses`` is empty, so a
    pinning fetcher has nothing to pin and must resolve at connect time — where the
    connection itself fails for a name that genuinely does not resolve.
    """

    host: str
    addresses: tuple[str, ...] = ()
    resolved: bool = True
    note: str = ""

    @property
    def pinnable(self) -> bool:
        """Whether a fetcher can connect by address instead of by name."""
        return bool(self.addresses)


def resolve_and_check(url: str, *, resolve: Resolver = system_resolver) -> Resolution:
    """:func:`check_url`, then the same check again on what the name resolves to.

    This is the half :func:`check_url` cannot do. ``http://evil.example.com/`` passes
    every literal test in this module and is the standard way to reach a metadata
    endpoint anyway: publish an ``A`` record pointing at ``169.254.169.254`` and let
    the victim's own resolver do the work. Nothing about the URL looks wrong, because
    nothing about it *is* wrong — the address is.

    Refuses if **any** returned address is disqualified, not merely if all of them
    are. A name that answers with one public address and one loopback address is not
    a name that is half safe; it is a name whose answer depends on which address the
    client happens to try, and an attacker choosing the mix gets to pick.

    Raises :exc:`UrlNotAllowed` for a literal-form failure (via :func:`check_url`) or
    for a hostile address. Returns a :class:`Resolution` otherwise, including for the
    case where resolution failed — see that class for why that is not a refusal.
    """
    checked = check_url(url)
    host = split_host(checked).lower()
    literal = parse_address(host)
    if literal is not None:
        # Already an address, already vetted by `check_url`. Returned as pinnable so a
        # fetcher has one path for both shapes rather than two.
        return Resolution(host=host, addresses=(str(literal),))
    try:
        found = tuple(resolve(host))
    except OSError as exc:
        return Resolution(host=host, resolved=False, note=str(exc))
    if not found:
        return Resolution(host=host, resolved=False, note="the resolver returned no addresses")
    for text in found:
        address = parse_address(text)
        if address is None:
            raise UrlNotAllowed(
                f"refusing to fetch {redact_url(checked)!r}: its host resolved to "
                f"{text!r}, which is not an address this program can classify"
            )
        reason = address_reason(address)
        if reason:
            raise UrlNotAllowed(
                f"refusing to fetch {redact_url(checked)!r}: the name {host!r} resolves "
                f"to {text}, which is {reason}. A public hostname pointing at an "
                f"internal address is how a fetch tool is turned into a request from "
                f"inside your network — the URL looks fine, and that is the point."
            )
    return Resolution(host=host, addresses=found)


__all__ = [
    "ALLOWED_SCHEMES",
    "BLOCKED_NAMES",
    "BLOCKED_SUFFIXES",
    "EXTRA_BLOCKED",
    "REDACTED",
    "Resolution",
    "Resolver",
    "UrlNotAllowed",
    "address_reason",
    "check_url",
    "host_reason",
    "parse_address",
    "redact_url",
    "resolve_and_check",
    "split_host",
    "system_resolver",
]
