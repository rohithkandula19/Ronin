"""Where a fetch may go, and what a URL may say in a log.

The check this replaced compared the host against five literal spellings of localhost,
so every row in :data:`MUST_BLOCK` reached the fetcher — including
``http://169.254.169.254/``, which answers unauthenticated HTTP with the instance's
role credentials on every major cloud. That is the single most valuable target
reachable from inside a trusted network, and a fetch tool driven by a model reading
attacker-controlled pages is exactly the way it gets requested.

Two tables rather than a handful of examples, for the same reason the command suites
are tables: the blocked list is a claim about a *class* of address, and a class is
only demonstrated by covering the notations it can be written in. Loopback alone has
five spellings here, and four of them passed the old check.

The benign table matters just as much. A URL policy that blocks the real internet gets
switched off, and then it protects nothing — the same argument the safety package
makes about the allowlist being cheap to say yes to.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Final

import pytest

from ronin.safety.net import (
    EXTRA_BLOCKED,
    REDACTED,
    Resolution,
    Resolver,
    UrlNotAllowed,
    address_reason,
    check_url,
    embedded_ipv4,
    host_reason,
    parse_address,
    redact_url,
    resolve_and_check,
    split_host,
    system_resolver,
)

#: Every one of these reached the fetcher before this change. The second field is what
#: makes the row interesting, so a failure names the property that stopped working
#: rather than just an address.
MUST_BLOCK: tuple[tuple[str, str], ...] = (
    # The reason this module exists.
    ("http://169.254.169.254/latest/meta-data/iam/security-credentials/", "AWS metadata"),
    ("http://[fd00:ec2::254]/latest/meta-data/", "AWS metadata over IPv6"),
    ("http://metadata.google.internal/computeMetadata/v1/", "GCP metadata, by name"),
    ("http://169.254.169.254/metadata/instance?api-version=2021-02-01", "Azure metadata"),
    # Loopback, in the notations a URL parser accepts.
    ("http://127.0.0.1:8080/", "loopback, dotted quad"),
    ("http://127.1/", "loopback, two parts — the last fills three octets"),
    ("http://2130706433/", "loopback as a 32-bit integer"),
    ("http://0x7f000001/", "loopback in hex"),
    ("http://0177.0.0.1/", "loopback with an octal first octet"),
    ("http://[::1]/", "loopback over IPv6"),
    ("http://[::ffff:127.0.0.1]/", "loopback as an IPv4-mapped IPv6 address"),
    ("http://localhost/admin", "loopback by name"),
    ("http://0.0.0.0:9000/", "the unspecified address, which routes here"),
    # The user's own network.
    ("http://10.0.0.5/admin", "private: 10/8"),
    ("http://172.16.4.1/", "private: 172.16/12"),
    ("http://192.168.1.1/setup", "private: 192.168/16"),
    ("http://[fc00::1]/", "private: IPv6 unique local"),
    ("http://[fe80::1]/", "link-local over IPv6"),
    ("http://100.64.0.1/", "carrier-grade NAT — shared, and ipaddress calls it public"),
    ("http://printer.local/", "an mDNS name on the LAN"),
    ("http://build.internal/", "an internal name by convention"),
    ("http://router.home.arpa/", "the reserved home network suffix"),
    ("http://224.0.0.1/", "multicast is not a host"),
    # The host is the last @-separated field, not the first.
    ("http://docs.example.com@169.254.169.254/", "userinfo dressed up as the host"),
    ("http://user:pass@127.0.0.1/", "userinfo with a password, pointing home"),
    # Not http(s) at all.
    ("file:///etc/passwd", "a file read wearing a URL"),
    ("gopher://example.com/1", "a scheme this program does not fetch"),
    ("ftp://example.com/x", "likewise"),
    ("", "empty"),
    ("http://", "a scheme and nothing else"),
    # Fail closed rather than guess.
    ("http://exa mple.com/", "a space in the host"),
    ("http://-example.com/", "a label starting with a hyphen"),
    ("http://exam_ple.com/", "an underscore is not legal in a hostname"),
    ("http://1.2.3.4.5/", "five parts is not an address and not a name"),
    ("http://999.1.1.1/", "an octet out of range"),
    ("http://10.16777216/", "a final part too large for the octets it has to fill"),
    ("http://[4000::1]/", "IPv6 space the RFCs reserve and nothing has assigned"),
    # An IPv4 destination written in IPv6, so a v6-shaped check does not read it.
    ("http://[2002:a9fe:a9fe::]/latest/meta-data/", "6to4 wrapping the metadata endpoint"),
    ("http://[2002:7f00:1::]/", "6to4 wrapping loopback"),
    ("http://[2001:0:53aa:64c::5601:5601]/", "Teredo whose client half is the metadata endpoint"),
    ("http://[64:ff9b::a9fe:a9fe]/", "NAT64 wrapping the metadata endpoint"),
    ("http://[::a9fe:a9fe]/", "the deprecated IPv4-compatible form"),
    ("http://[2001:470:1f0b:1:0:5efe:a9fe:a9fe]/", "ISATAP under a public prefix"),
    ("http://[2606:4700::5efe:a00:5]/", "ISATAP wrapping a private address"),
    ("http://[2001:470:1f0b:1:200:5efe:c0a8:101]/", "ISATAP with the universal bit set"),
)

#: The public internet, which must keep working. A false positive here is how a
#: security control gets switched off.
MUST_PASS: tuple[str, ...] = (
    "https://example.com/CHANGELOG.md",
    "https://docs.python.org/3/library/ipaddress.html#ipaddress.ip_address",
    "http://93.184.216.34/",
    "https://api.github.com/repos/o/r/pulls?state=open&per_page=5",
    "https://sub.domain.example.co.uk/a/b/c",
    "https://xn--bcher-kva.example/",
    "https://example.com:8443/path",
    "http://example.com./trailing-root-dot",
    "https://8.8.8.8/",
    "https://[2606:4700::1]/",
    "HTTPS://Example.COM/Mixed-Case",
    "https://user@example.com/userinfo-on-a-public-host",
    # A tunnel form carrying a *public* IPv4 address reaches the public internet, so
    # unwrapping it must not turn into a refusal. The boundary of the rule below.
    "https://[2606:4700::5efe:5db8:d822]/",
)


# --------------------------------------------------------------------------- #
# the two tables
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("url", "why"), MUST_BLOCK, ids=[why for _, why in MUST_BLOCK])
def test_every_inward_pointing_url_is_refused(url: str, why: str) -> None:
    with pytest.raises(UrlNotAllowed):
        check_url(url)


@pytest.mark.parametrize("url", MUST_PASS)
def test_the_public_internet_still_works(url: str) -> None:
    """Returned unchanged apart from surrounding whitespace: this function decides,
    it does not rewrite. A checker that normalised the URL would mean the string
    fetched was not the string the user read in the approval prompt."""
    assert check_url(f"  {url}  ") == url


# --------------------------------------------------------------------------- #
# the classifier, one property at a time
# --------------------------------------------------------------------------- #


def test_the_legacy_numeric_notations_resolve_the_way_curl_resolves_them() -> None:
    """``inet_aton`` semantics, implemented here rather than delegated: the final part
    fills every octet the earlier parts did not name, so ``10.1`` is ``10.0.0.1`` and
    not ``10.1.0.0``. Getting that backwards would block a public address and pass a
    private one."""
    assert parse_address("2130706433") == ipaddress.IPv4Address("127.0.0.1")
    assert parse_address("10.1") == ipaddress.IPv4Address("10.0.0.1")
    assert parse_address("172.16.1") == ipaddress.IPv4Address("172.16.0.1")
    assert parse_address("0x0a.0.0.1") == ipaddress.IPv4Address("10.0.0.1")
    assert parse_address("0177.0.0.1") == ipaddress.IPv4Address("127.0.0.1")


def test_the_numeric_parser_is_not_fooled_into_reading_a_hostname_as_an_address() -> None:
    """A name must come back as a name, or every hostname would be classified by a
    parse that failed. ``None`` here means "this is a name", which is what sends
    ``host_reason`` down the DNS-name branch."""
    for host in ("example.com", "1.2.3.4.5", "999.1.1.1", "0x1.example.com", "-1.2.3.4"):
        assert parse_address(host) is None, host


def test_a_mapped_address_is_unwrapped_so_one_check_covers_both_spellings() -> None:
    """``::ffff:127.0.0.1`` is loopback wearing IPv6. Unwrapping it means the IPv4
    rules apply once, rather than every rule needing an IPv6 twin that could drift."""
    assert parse_address("[::ffff:127.0.0.1]") == ipaddress.IPv4Address("127.0.0.1")
    assert parse_address("[::ffff:10.0.0.1]") == ipaddress.IPv4Address("10.0.0.1")


def test_each_disqualifying_property_gives_its_own_reason() -> None:
    """The message is the model's only feedback, and "it points at a private address"
    tells it to stop trying while "refused" invites the same call with a synonym."""
    assert "loopback" in address_reason(ipaddress.ip_address("127.0.0.1"))
    assert "link-local" in address_reason(ipaddress.ip_address("169.254.169.254"))
    assert "private" in address_reason(ipaddress.ip_address("10.0.0.1"))
    assert "multicast" in address_reason(ipaddress.ip_address("224.0.0.1"))
    assert "unspecified" in address_reason(ipaddress.ip_address("0.0.0.0"))
    assert address_reason(ipaddress.ip_address("93.184.216.34")) == ""


# --------------------------------------------------------------------------- #
# an IPv4 address wearing IPv6
# --------------------------------------------------------------------------- #

#: The transition forms that carry an IPv4 address through IPv6 syntax, each written the
#: way someone reaching for a metadata endpoint would write it. Third field is the word
#: the refusal has to contain for a reader to understand what they are looking at: the
#: hex says nothing, and "a private address" alone does not explain where the IPv4 came
#: from.
EMBEDDING: tuple[tuple[str, str, str], ...] = (
    ("2002:a9fe:a9fe::", "169.254.169.254", "6to4"),
    ("2002:c0a8:101::", "192.168.1.1", "6to4"),
    ("2001:0:53aa:64c::5601:5601", "169.254.169.254", "Teredo"),
    ("64:ff9b::a9fe:a9fe", "169.254.169.254", "NAT64"),
    ("::a9fe:a9fe", "169.254.169.254", "IPv4-compatible"),
    ("2001:470:1f0b:1:0:5efe:a9fe:a9fe", "169.254.169.254", "ISATAP"),
    ("2606:4700::5efe:a00:5", "10.0.0.5", "ISATAP"),
    ("2001:470:1f0b:1:200:5efe:c0a8:101", "192.168.1.1", "ISATAP"),
)


@pytest.mark.parametrize(
    ("outer", "inner", "form"), EMBEDDING, ids=[f"{f[2]}-{f[1]}" for f in EMBEDDING]
)
def test_the_ipv4_address_inside_an_ipv6_address_is_unwrapped_and_named(
    outer: str, inner: str, form: str
) -> None:
    """Six IPv6 forms exist to carry a v4 address, and each is a spelling of a v4
    destination that a check reading only the v6 prefix does not see. The refusal names
    the form and the address inside it, because otherwise the reader has to know that
    ``2002:a9fe:a9fe::`` is 169.254.169.254 in hex to understand the answer."""
    address = parse_address(outer)
    assert address is not None
    carried = [str(item.address) for item in embedded_ipv4(address)]
    assert inner in carried, f"{outer} carries {inner}"
    reason = address_reason(address)
    assert form in reason and inner in reason, reason


def test_isatap_is_the_form_no_prefix_test_can_find() -> None:
    """The one this unwrapping actually closes, and the reason it was worth writing.

    6to4, Teredo, NAT64 and ``::a.b.c.d`` all sit in prefixes the property chain already
    refuses, so unwrapping them only improves the message. An ISATAP address wears the
    *site's own* global unicast prefix — every property below is false and the address is
    not in :data:`EXTRA_BLOCKED` — and on a host with an ISATAP interface up it routes to
    the IPv4 address in its interface identifier. Nothing in a prefix test can see it.
    """
    address = ipaddress.IPv6Address("2001:470:1f0b:1:0:5efe:a9fe:a9fe")
    assert not any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    ), "indistinguishable from a public address by every property"
    assert not any(address in network for network in EXTRA_BLOCKED), "and by prefix"
    assert "169.254.169.254" in address_reason(address), "and refused anyway"


def test_unwrapping_adds_refusals_and_never_grants_permission() -> None:
    """The direction of the rule, which is the whole design.

    ``2002:5db8:d822::`` is a 6to4 address wrapping 93.184.216.34 — a public IPv4. Read
    payload-first it looks fine, and judging tunnels by their payload alone would open
    every 6to4 and Teredo address whose payload happens to be public. The outer form is
    itself a reason not to go there, so the prefix verdict stands and the unwrapping only
    ever *adds* a reason.
    """
    for outer in ("2002:5db8:d822::", "2001:0:53aa:64c::a2ff:2922", "64:ff9b::5db8:d822"):
        address = parse_address(outer)
        assert address is not None
        assert address_reason(address) != "", outer


def test_a_teredo_address_carries_two_and_the_client_half_is_deobfuscated() -> None:
    """Teredo embeds the relay's IPv4 in the prefix and the client's in the last 32 bits,
    XORed with all ones (RFC 4380 §4) — so ``5601:5601`` *is* 169.254.169.254 and a check
    reading those bytes literally sees 86.1.86.1. :mod:`ipaddress` undoes the XOR, which
    is the argument for asking the stdlib rather than slicing bytes here."""
    carried = embedded_ipv4(ipaddress.IPv6Address("2001:0:53aa:64c::5601:5601"))
    assert [str(item.address) for item in carried] == ["83.170.6.76", "169.254.169.254"]
    assert [item.role for item in carried] == ["relay ", "client "]


def test_a_mapped_address_reports_what_it_wraps_even_though_the_parser_unwraps_it() -> None:
    """``parse_address`` turns ``::ffff:127.0.0.1`` into 127.0.0.1 before the classifier
    sees it, so this form never reaches ``address_reason`` by that route. Handled anyway,
    because a caller holding an ``IPv6Address`` from somewhere else — a resolver answer
    parsed by other code, a future call site — must get the same verdict."""
    address = ipaddress.IPv6Address("::ffff:127.0.0.1")
    assert [str(item.address) for item in embedded_ipv4(address)] == ["127.0.0.1"]
    assert "loopback" in address_reason(address)


def test_an_address_that_wraps_nothing_says_so() -> None:
    """``::`` and ``::1`` are not a wrapped ``0.0.0.0`` and ``0.0.0.1``; they are the
    unspecified and loopback addresses, refused on their own properties. Reporting a
    wrapped address for them would put a false statement in a refusal message. An IPv4
    address wraps nothing by construction."""
    for text in ("::", "::1", "2606:4700:4700::1111", "10.0.0.1", "93.184.216.34"):
        assert embedded_ipv4(ipaddress.ip_address(text)) == (), text


def test_an_unclassifiable_host_fails_closed() -> None:
    """The half that makes the rest safe. If a host is neither a name we can validate
    nor an address we can parse, refusing is the only answer that does not depend on
    a resolver agreeing with a guess — and a bypass in this code would be written as
    exactly such a notation."""
    reason = host_reason("exa mple.com")
    assert "cannot classify" in reason or "refused rather than guessed" in reason


def test_the_host_is_the_last_userinfo_field_not_the_first() -> None:
    """``http://docs.example.com@169.254.169.254/`` is a request to the metadata
    endpoint that reads like a request to a documentation site. A checker that split
    on the first ``@`` would be reading the attacker's decoration and blessing it."""
    assert split_host("http://docs.example.com@169.254.169.254/x") == "169.254.169.254"
    assert split_host("http://user:pw@example.com:8443/x") == "example.com"
    assert split_host("http://[::1]:8080/x") == "[::1]"
    assert split_host("https://example.com") == "example.com"
    assert split_host("http://example.com?a=b") == "example.com"


def test_a_blocked_address_is_blocked_on_every_port() -> None:
    """The port is discarded on purpose: an admin panel on 8080 is not less inward
    than one on 80, and a port allowlist would be a second thing to maintain."""
    for port in ("", ":80", ":8080", ":1", ":65535"):
        with pytest.raises(UrlNotAllowed):
            check_url(f"http://127.0.0.1{port}/")


# --------------------------------------------------------------------------- #
# redaction
# --------------------------------------------------------------------------- #


def test_every_query_value_goes_and_every_name_stays() -> None:
    """The broad rule, and the reason for it: "no query value is logged" is one
    sentence and this one assertion checks it, while "no *secret* query value" is a
    list of parameter names that grows with every API and fails silently when it
    misses one. Names survive so a log still shows the shape of the call."""
    assert redact_url("https://api.example.com/v1?token=abc&page=2") == (
        f"https://api.example.com/v1?token={REDACTED}&page={REDACTED}"
    )


def test_userinfo_and_fragment_are_removed_entirely() -> None:
    """A password has no name to keep, and an OAuth implicit-flow fragment is a live
    access token. Neither has a form worth preserving in a log."""
    assert redact_url("https://user:pw@example.com/x") == "https://example.com/x"
    assert redact_url("https://example.com/cb#access_token=zzz") == (
        f"https://example.com/cb#{REDACTED}"
    )


def test_a_url_with_nothing_secret_in_it_is_untouched() -> None:
    """Byte-identical for the common case. A redactor that rewrote ordinary URLs
    would make every log line slightly untrue, and nobody would trust the ones that
    mattered."""
    for url in ("https://example.com/a/b", "http://example.com", "https://example.com/#"):
        assert redact_url(url) == url.rstrip("#") or redact_url(url) == url


def test_an_empty_query_field_does_not_become_a_bare_equals() -> None:
    """``?a=1&&b=2`` has an empty field in the middle, which a naive split turns into
    a stray ``=<redacted>``. A redacted URL is often the only record of a call, so it
    should not acquire parameters the request never had."""
    assert redact_url("https://e.com/x?a=1&&b=2") == (f"https://e.com/x?a={REDACTED}&b={REDACTED}")


def test_a_valueless_flag_keeps_its_shape() -> None:
    """``?verbose`` carries no value, so there is nothing to redact and no ``=`` to
    invent."""
    assert redact_url("https://e.com/x?verbose") == "https://e.com/x?verbose"


def test_redaction_is_idempotent() -> None:
    """Logs get re-processed, and a second pass must not turn ``<redacted>`` into
    something else or start stripping the parameter names too."""
    once = redact_url("https://api.example.com/v1?token=abc&page=2#frag")
    assert redact_url(once) == once


def test_a_rejected_url_is_redacted_in_the_refusal_message() -> None:
    """The case that is easy to miss: a refusal is text that gets shown to the user,
    written to a transcript and pasted into an issue. A secret in a URL we declined to
    fetch is still a secret, and declining is not a reason to print it."""
    with pytest.raises(UrlNotAllowed) as caught:
        check_url("http://169.254.169.254/latest?token=SUPERSECRET")
    message = str(caught.value)
    assert "SUPERSECRET" not in message
    assert REDACTED in message
    assert "169.254.169.254" in message, "the user must still see where it pointed"


def test_the_refusal_says_what_to_do_instead() -> None:
    """A refusal the model cannot act on becomes a retry of the same call, which is
    the failure mode the safety package's docstring warns about."""
    with pytest.raises(UrlNotAllowed, match="bash"):
        check_url("http://localhost:3000/")


# --------------------------------------------------------------------------- #
# the seam
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# resolution: the half `check_url` cannot do
# --------------------------------------------------------------------------- #


def resolver(answers: dict[str, tuple[str, ...]]) -> Resolver:
    """A fake DNS. Every test injects one, so the suite never resolves a real name.

    A name that is not in ``answers`` raises ``OSError``, which is what the system
    resolver does for NXDOMAIN — the case the policy deliberately lets through.
    """

    def resolve(host: str) -> tuple[str, ...]:
        if host not in answers:
            raise OSError(f"Name or service not known: {host}")
        return answers[host]

    return resolve


PUBLIC: Final = ("93.184.216.34",)


def test_a_public_name_pointing_at_the_metadata_endpoint_is_refused() -> None:
    """The attack `check_url` cannot see, and the reason this function exists.

    Nothing about ``https://docs.example.com/`` is malformed, so every literal check in
    the module passes it. Publishing an ``A`` record that points at 169.254.169.254 is
    the ordinary way to reach a metadata endpoint from someone else's network — the
    victim's own resolver does the work.
    """
    resolve = resolver({"docs.example.com": ("169.254.169.254",)})
    with pytest.raises(UrlNotAllowed) as caught:
        resolve_and_check("https://docs.example.com/x", resolve=resolve)
    message = str(caught.value)
    # The whole clause, not a bare substring. Stronger, because the claim is that the
    # refusal *explains itself* — name, address and property in one sentence — and a
    # substring check passes on a message that merely happens to contain the name
    # somewhere. (CodeQL also reads `"host" in some_string` as URL sanitization, which
    # is the right instinct on a real authorization check and wrong here; this phrasing
    # is both more precise and not that shape.)
    assert "the name 'docs.example.com' resolves to 169.254.169.254" in message
    assert "link-local" in message
    assert "internal address" in message, "and says why that matters"


def test_a_public_name_pointing_at_a_public_address_is_allowed_and_pinnable() -> None:
    """The control. A policy that blocked this would be switched off within a day."""
    found = resolve_and_check(
        "https://docs.example.com/x", resolve=resolver({"docs.example.com": PUBLIC})
    )
    assert isinstance(found, Resolution)
    assert found.host == "docs.example.com"
    assert found.addresses == PUBLIC
    assert found.resolved is True
    assert found.pinnable is True


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        (("127.0.0.1",), "loopback"),
        (("10.0.0.5",), "private"),
        (("169.254.169.254",), "the metadata endpoint"),
        (("::1",), "loopback over IPv6"),
        (("::ffff:10.0.0.1",), "a mapped private address"),
        (("100.64.0.1",), "carrier-grade NAT"),
        (("0.0.0.0",), "the unspecified address"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_every_hostile_answer_is_refused_whatever_the_name_looked_like(
    answer: tuple[str, ...], why: str
) -> None:
    """The resolved address is judged by exactly the same rules as a literal one, so
    there is one classifier and not two that can drift."""
    with pytest.raises(UrlNotAllowed):
        resolve_and_check("https://a.example.com/", resolve=resolver({"a.example.com": answer}))


def test_a_name_resolving_to_a_tunnel_address_is_refused_too() -> None:
    """The two halves compose: the resolved answer goes through the same classifier, so a
    name with an ``AAAA`` record pointing at an ISATAP address gets the same verdict as
    the literal. This is the shape the attack would actually take — nobody types hex into
    a prompt when they can publish a record."""
    resolve = resolver({"docs.example.com": ("2001:470:1f0b:1:0:5efe:a9fe:a9fe",)})
    with pytest.raises(UrlNotAllowed) as caught:
        resolve_and_check("https://docs.example.com/x", resolve=resolve)
    assert "ISATAP" in str(caught.value)
    assert "169.254.169.254" in str(caught.value)


def test_a_mixed_answer_is_refused_rather_than_partially_trusted() -> None:
    """One public address and one loopback address is not half safe.

    It is a name whose answer depends on which address the client happens to try, and
    an attacker who controls the record controls the mix. Refusing on *any* bad address
    is the only reading that does not depend on connection-ordering luck.
    """
    resolve = resolver({"mixed.example.com": ("93.184.216.34", "127.0.0.1")})
    with pytest.raises(UrlNotAllowed, match=re.escape("127.0.0.1")):
        resolve_and_check("https://mixed.example.com/", resolve=resolve)


def test_both_address_families_are_checked() -> None:
    """A harmless ``A`` record with a loopback ``AAAA`` record reaches this machine on
    any client that prefers IPv6, which is most of them."""
    resolve = resolver({"dual.example.com": ("93.184.216.34", "::1")})
    with pytest.raises(UrlNotAllowed):
        resolve_and_check("https://dual.example.com/", resolve=resolve)


def test_a_url_that_fails_the_literal_check_never_reaches_the_resolver() -> None:
    """Order matters: the cheap, offline, certain check runs first. Resolving a URL we
    were always going to refuse is a DNS query made on an attacker's behalf."""
    asked: list[str] = []

    def spy(host: str) -> tuple[str, ...]:
        asked.append(host)
        return PUBLIC

    with pytest.raises(UrlNotAllowed):
        resolve_and_check("http://169.254.169.254/latest/meta-data/", resolve=spy)
    with pytest.raises(UrlNotAllowed):
        resolve_and_check("file:///etc/passwd", resolve=spy)
    assert asked == []


def test_a_literal_address_is_pinnable_without_asking_dns() -> None:
    """``http://93.184.216.34/`` has nothing to resolve. It still returns a pinnable
    resolution so a fetcher has one code path for both shapes rather than two."""
    asked: list[str] = []

    def spy(host: str) -> tuple[str, ...]:
        asked.append(host)
        return ()

    found = resolve_and_check("http://93.184.216.34/", resolve=spy)
    assert found.addresses == ("93.184.216.34",)
    assert found.pinnable is True
    assert asked == [], "an address is already an address"


def test_an_unresolvable_name_is_allowed_through_and_says_so() -> None:
    """The deliberate weakness, pinned so it cannot change by accident.

    Refusing here would make every fetch on a machine with a flaky resolver a security
    refusal, which reads as a bug and gets the check disabled. The accepted cost is
    written into ``Resolution``: someone who can make resolution fail for a name they
    control gets the same pass as a name that does not exist. What limits it is
    ``pinnable`` being ``False`` — there is nothing to pin, so a fetcher connects by
    name and a name that truly does not resolve fails there.
    """
    found = resolve_and_check("https://nope.example.com/", resolve=resolver({}))
    assert found.resolved is False
    assert found.pinnable is False
    assert found.addresses == ()
    assert "Name or service not known" in found.note


def test_a_resolver_that_answers_with_nothing_is_treated_as_a_failure() -> None:
    """An empty tuple is not "no addresses are bad" — it is "we learned nothing", and
    it must not read as a clean bill of health."""
    found = resolve_and_check(
        "https://empty.example.com/", resolve=resolver({"empty.example.com": ()})
    )
    assert found.resolved is False
    assert found.pinnable is False
    assert "no addresses" in found.note


def test_an_answer_that_is_not_an_address_at_all_is_refused() -> None:
    """Fail closed on nonsense from the resolver, the same way the literal check fails
    closed on a host it cannot classify."""
    resolve = resolver({"weird.example.com": ("not-an-address",)})
    with pytest.raises(UrlNotAllowed, match="classify"):
        resolve_and_check("https://weird.example.com/", resolve=resolve)


def test_a_token_in_the_url_is_redacted_in_a_resolution_refusal_too() -> None:
    """Same rule as every other refusal in this module: the message gets shown, logged
    and pasted into issues, and the URL we declined can still carry a live secret."""
    resolve = resolver({"docs.example.com": ("10.0.0.1",)})
    with pytest.raises(UrlNotAllowed) as caught:
        resolve_and_check("https://docs.example.com/api?token=SUPERSECRET", resolve=resolve)
    assert "SUPERSECRET" not in str(caught.value)
    assert REDACTED in str(caught.value)


def test_the_system_resolver_is_the_only_thing_that_touches_dns() -> None:
    """A named default, not an inline call — which is what makes every test above
    offline. Asserted by identity so a refactor cannot quietly inline it."""
    import inspect

    signature = inspect.signature(resolve_and_check)
    assert signature.parameters["resolve"].default is system_resolver


def test_the_policy_is_reachable_from_the_package_root() -> None:
    """Exported because anything that builds a ``Fetcher`` needs it, and an
    unexported check is one that gets reimplemented — badly — at the next call
    site."""
    from ronin.safety import check_url as exported
    from ronin.safety import redact_url as exported_redact

    assert exported is check_url
    assert exported_redact is redact_url


def test_the_tool_layer_asks_this_module_rather_than_keeping_its_own_copy() -> None:
    """``tools/net.py`` had the copy that went stale. Pinning the identity here means
    a future edit to the tool's checker cannot quietly diverge from the policy.

    Read out of ``__dict__`` because ``check_url`` is an *import* in that module, not
    something it defines, and mypy runs with implicit re-export disabled — the
    attribute exists at runtime but is deliberately not part of the module's public
    surface, which is exactly the arrangement this test wants to confirm.
    """
    from ronin.tools import net as tool_net

    assert tool_net.__dict__["check_url"] is check_url


# --------------------------------------------------------------------------- #
# The numeric parser's guards, each of which a mutation sweep could remove with
# the suite still green
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        # A hex octet beside decimal ones — the mixed spelling, which no test reached.
        ("0x7f.0.0.1", "127.0.0.1"),
        ("0xff.255.255.255", "255.255.255.255"),
        # An octet of exactly 255 in a *leading* position, which is the boundary the
        # `value > 0xFF` guard sits on. Reached only through a spelling `ipaddress`
        # itself rejects, which is why the plain dotted quad never exercised it.
        ("0xff.0.0.1", "255.0.0.1"),
    ],
)
def test_a_hex_octet_beside_decimal_ones_still_resolves(host: str, expected: str) -> None:
    assert parse_address(host) == ipaddress.IPv4Address(expected)


@pytest.mark.parametrize("host", ["10.999.0.1", "256.0.0.1", "0x100.0.0.1"])
def test_an_octet_past_255_is_not_an_address(host: str) -> None:
    """The guard the sweep could delete silently.

    Without it the octets are packed anyway and the parser invents a *different*
    address — which for a check that decides "is this private?" is worse than refusing,
    because the answer would be about somewhere else entirely.
    """
    assert parse_address(host) is None


@pytest.mark.parametrize("host", ["-0x7f000001", "-0x1.2.3.4", "-1"])
def test_a_negative_part_is_not_an_address(host: str) -> None:
    """``-0x`` is accepted by the hex branch on purpose — ``int("-0x10", 16)`` parses —
    so something downstream has to reject the negative it produces. Nothing tested that
    it did."""
    assert parse_address(host) is None


@pytest.mark.parametrize("host", ["[::1", "::1]", "[", "]"])
def test_a_half_bracketed_host_is_not_unwrapped(host: str) -> None:
    """Brackets are the URL's syntax around an IPv6 literal, and only a matched pair is.
    Stripping one-sided brackets would turn ``[::1`` into ``::``, which parses — a
    malformed host silently becoming a real address."""
    assert parse_address(host) is None


def test_a_name_with_the_maximum_label_length_is_still_a_name() -> None:
    # 253 is the boundary in `host_reason`, and `>` versus `>=` there was invisible to
    # the suite. A name of exactly 253 characters is legal and must classify as a name.
    label = "a" * 63
    name = ".".join([label, label, label, "a" * 61])
    assert len(name) == 253
    assert "not a hostname" not in host_reason(name)
    assert "not a hostname" in host_reason(f"{name}x")
