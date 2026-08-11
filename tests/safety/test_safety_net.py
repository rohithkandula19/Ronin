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
    REDACTED,
    Resolution,
    Resolver,
    UrlNotAllowed,
    address_reason,
    check_url,
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
    assert "docs.example.com" in message
    assert "169.254.169.254" in message, "the address is the evidence; it has to be shown"
    assert "link-local" in message


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
