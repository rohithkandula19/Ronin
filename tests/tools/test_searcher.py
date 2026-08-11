"""The three search backends, driven with no network and no key.

``web_search`` was the last tool in the tree with no implementation behind its injected
callable. The reason it stayed that way is not technical: a searcher needs a provider,
and choosing one is a decision about somebody's account. So the code is a *table* of
three, and these tests are mostly about the table being honest — that each provider's
request goes where its API documents, that each response shape parses, and that a
response which is *not* that shape says so instead of returning nothing.

Two properties get more attention than the rest, because they are the ones a plausible
implementation gets wrong:

* **The query never reaches the URL's host or path.** It is percent-encoded into a
  parameter or placed in a JSON field, so no query a model can write moves the request
  to another server.
* **A configured endpoint is not a model-chosen URL.** A SearXNG on ``localhost`` is
  reachable — the user put it there — while a bad *scheme* in the same config value is
  still refused. That asymmetry is deliberate and is the thing to keep pinned.

Offline by construction: ``socket`` may not be imported in this suite at all, so the
transport arrives as a fake connector and DNS as a fake resolver.
"""

from __future__ import annotations

import email.message
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

import pytest

from ronin.safety.net import Resolver
from ronin.tools.searcher import (
    BRAVE,
    DEFAULT_LIMIT,
    PROVIDERS,
    SEARXNG,
    TAVILY,
    Provider,
    SearchError,
    SearchResult,
    build_request,
    parse_results,
    provider_named,
    provider_searcher,
    search_sync,
)

PUBLIC: Final = "93.184.216.34"

#: One response per provider, in the shape its API actually returns. These are the
#: fixtures the adapters are written against, so a provider changing its schema shows up
#: as a failure here rather than as an empty result list in front of a user.
BRAVE_PAYLOAD: Final = {
    "web": {
        "results": [
            {
                "title": "ripgrep user guide",
                "url": "https://example.com/rg/guide",
                "description": "--multiline enables patterns spanning lines",
            },
            {"title": "second", "url": "https://example.com/two", "description": "more"},
        ]
    }
}

TAVILY_PAYLOAD: Final = {
    "query": "ripgrep multiline",
    "results": [
        {
            "title": "ripgrep user guide",
            "url": "https://example.com/rg/guide",
            "content": "A longer, agent-shaped snippet than a search engine returns.",
        }
    ],
}

SEARXNG_PAYLOAD: Final = {
    "results": [
        {
            "title": "ripgrep user guide",
            "url": "https://example.com/rg/guide",
            "content": "from whichever engines the instance aggregates",
        }
    ]
}

PAYLOADS: Final = {"brave": BRAVE_PAYLOAD, "tavily": TAVILY_PAYLOAD, "searxng": SEARXNG_PAYLOAD}


def resolver(answer: tuple[str, ...] = (PUBLIC,)) -> Resolver:
    def resolve(_host: str) -> tuple[str, ...]:
        return answer

    return resolve


def headers(**fields: str) -> email.message.Message:
    message = email.message.Message()
    for name, value in fields.items():
        message[name.replace("_", "-")] = value
    return message


@dataclass
class FakeRaw:
    status: int
    headers: email.message.Message
    body: bytes

    def read(self, amt: int | None = None) -> bytes:
        return self.body if amt is None else self.body[:amt]


@dataclass
class Sent:
    """What the transport was asked to do — the record these tests assert against."""

    host: str
    port: int | None
    address: str
    tls: bool
    method: str = ""
    path: str = ""
    body: bytes | None = None
    sent_headers: Mapping[str, str] = field(default_factory=dict)


@dataclass
class FakeNetwork:
    payload: Any = None
    status: int = 200
    raw_body: bytes | None = None
    sent: list[Sent] = field(default_factory=list)

    def connector(
        self, *, host: str, port: int | None, address: str, tls: bool, timeout: float
    ) -> FakeNetwork._Connection:
        record = Sent(host=host, port=port, address=address, tls=tls)
        self.sent.append(record)
        return FakeNetwork._Connection(self, record)

    @dataclass
    class _Connection:
        network: FakeNetwork
        record: Sent

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
            self.record.sent_headers = dict(headers)

        def getresponse(self) -> FakeRaw:
            body = (
                self.network.raw_body
                if self.network.raw_body is not None
                else json.dumps(self.network.payload).encode()
            )
            return FakeRaw(
                status=self.network.status,
                headers=headers(Content_Type="application/json"),
                body=body,
            )

        def close(self) -> None:
            return None


def run(provider: Provider, query: str = "ripgrep multiline", **kwargs: Any) -> Any:
    """A search through the real code path, with the network faked."""
    net = kwargs.pop("net", None) or FakeNetwork(payload=PAYLOADS[provider.name])
    key = kwargs.pop("key", "test-key" if provider.needs_key else "")
    endpoint = kwargs.pop(
        "endpoint", "https://searx.example.com/search" if provider is SEARXNG else ""
    )
    results = search_sync(
        provider,
        query,
        key=key,
        endpoint=endpoint,
        resolve=resolver(),
        connect=net.connector,
        **kwargs,
    )
    return results, net


# --------------------------------------------------------------------------- #
# the table
# --------------------------------------------------------------------------- #


def test_every_provider_in_the_table_is_reachable_by_name() -> None:
    """The name is what a user types into ``RONIN_SEARCH_PROVIDER``, so it is part of
    the interface and worth pinning."""
    assert sorted(PROVIDERS) == ["brave", "searxng", "tavily"]
    for name, provider in PROVIDERS.items():
        assert provider_named(name) is provider
        assert provider_named(name.upper()) is provider, "typing it capitalised is fine"


def test_an_unknown_provider_names_the_ones_that_exist() -> None:
    """A typo is the commonest configuration error, and "unknown provider" without the
    list is a message that sends someone to the source."""
    with pytest.raises(SearchError) as caught:
        provider_named("gooogle")
    message = str(caught.value)
    assert "brave" in message and "tavily" in message and "searxng" in message
    assert "RONIN_SEARCH_PROVIDER" in message


@pytest.mark.parametrize("name", sorted(PROVIDERS))
def test_each_provider_parses_its_own_documented_response(name: str) -> None:
    """One fixture per provider, in the shape its API returns. A schema change surfaces
    here rather than as a silently empty result list."""
    provider = PROVIDERS[name]
    results, _net = run(provider)
    assert results, f"{name} parsed no results out of its own fixture"
    first = results[0]
    assert isinstance(first, SearchResult)
    assert first.title == "ripgrep user guide"
    assert first.url == "https://example.com/rg/guide"
    assert first.snippet, "a result with no snippet is a row the model cannot triage"


@pytest.mark.parametrize("name", sorted(PROVIDERS))
def test_each_provider_sends_its_credential_the_way_its_api_documents(name: str) -> None:
    """Brave wants a subscription token, Tavily a bearer, SearXNG nothing. Getting this
    wrong produces a 401 that looks like a bad key rather than a bad client."""
    provider = PROVIDERS[name]
    _results, net = run(provider)
    sent = net.sent[0].sent_headers
    if provider.name == "brave":
        assert sent["X-Subscription-Token"] == "test-key"
        assert "Authorization" not in sent
    elif provider.name == "tavily":
        assert sent["Authorization"] == "Bearer test-key"
    else:
        assert "Authorization" not in sent
        assert "X-Subscription-Token" not in sent
    assert sent["Accept"] == "application/json"


def test_a_get_provider_puts_the_query_in_a_parameter_and_a_post_provider_in_the_body() -> None:
    """The two shapes, asserted rather than assumed — a POST body sent as a query string
    is a 400 from the provider and an empty result list to the user."""
    _brave, brave_net = run(BRAVE)
    assert brave_net.sent[0].method == "GET"
    assert brave_net.sent[0].body is None
    assert "q=ripgrep%20multiline" in brave_net.sent[0].path

    _tavily, tavily_net = run(TAVILY)
    assert tavily_net.sent[0].method == "POST"
    body = json.loads(tavily_net.sent[0].body or b"{}")
    assert body["query"] == "ripgrep multiline"
    assert body["max_results"] == DEFAULT_LIMIT
    assert tavily_net.sent[0].sent_headers["Content-Type"] == "application/json"


# --------------------------------------------------------------------------- #
# the query cannot move the request
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "query",
    [
        "ripgrep multiline",
        "how do I use & and ? in a query",
        "https://evil.example.com/#",
        "a b&count=999&q=",
        "../../etc/passwd",
        "site:example.com foo",
        "\u2019 smart quotes and \u00e9mojis \U0001f389",
        "'; DROP TABLE results; --",
    ],
)
def test_no_query_can_change_which_host_is_contacted(query: str) -> None:
    """The property that matters most here. The query is model-supplied, so if it could
    reach the host or the path, ``web_search`` would be a way to make Ronin request an
    arbitrary URL — the whole thing `safety.net` exists to prevent, reintroduced through
    a text field."""
    _results, net = run(BRAVE, query)
    (sent,) = net.sent
    assert sent.host == "api.search.brave.com"
    assert sent.address == PUBLIC
    assert sent.path.startswith("/res/v1/web/search?")
    # Everything after the first `?` is parameters; the query's own characters must be
    # encoded rather than able to introduce a path segment or a second host.
    assert "://" not in sent.path.split("?", 1)[1].replace("%3A%2F%2F", "")


def test_a_query_is_encoded_rather_than_dropped() -> None:
    """Encoded, not stripped: a user searching for an exact error message with an
    ampersand in it needs the ampersand to arrive."""
    url, _headers, _body = build_request(BRAVE, "a & b ? c", key="k")
    assert "a%20%26%20b%20%3F%20c" in url


def test_a_post_provider_carries_the_query_verbatim_in_json() -> None:
    """No encoding needed inside JSON, and none applied — a doubly-escaped query is a
    query that finds nothing."""
    _url, _headers, body = build_request(TAVILY, "a & b ? c", key="k")
    assert json.loads(body or b"{}")["query"] == "a & b ? c"


# --------------------------------------------------------------------------- #
# a configured endpoint is not a model-chosen URL
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:8888/search",
        "http://127.0.0.1:8888/search",
        "http://192.168.1.50:8888/search",
        "http://searx.internal/search",
    ],
)
def test_a_self_hosted_instance_on_a_private_address_is_reachable(endpoint: str) -> None:
    """The point of supporting SearXNG at all. The model-URL policy refuses private
    addresses because a *model* must not reach inside the network; a searcher endpoint is
    configuration, exactly like the local model server Ronin has always dialled. Refusing
    it would mean the no-key provider only worked against somebody else's instance.
    """
    net = FakeNetwork(payload=SEARXNG_PAYLOAD)
    results = search_sync(
        SEARXNG,
        "ripgrep",
        endpoint=endpoint,
        resolve=resolver(("127.0.0.1",)),
        connect=net.connector,
    )
    assert results
    assert net.sent[0].tls is False


@pytest.mark.parametrize("endpoint", ["file:///etc/passwd", "gopher://x/1", "ftp://f/x"])
def test_a_bad_scheme_in_configuration_is_still_refused(endpoint: str) -> None:
    """The half of the policy a configured endpoint does *not* escape. `file://` in a
    config value is a typo worth naming, not a protocol to attempt — and it is checked
    through ``safety.net.check_scheme`` rather than re-implemented here, so there is one
    definition of which schemes this program speaks."""
    net = FakeNetwork(payload=SEARXNG_PAYLOAD)
    with pytest.raises(SearchError, match="unusable"):
        search_sync(SEARXNG, "q", endpoint=endpoint, resolve=resolver(), connect=net.connector)
    assert net.sent == [], "nothing was contacted"


def test_the_endpoint_is_still_pinned_to_the_address_it_resolved_to() -> None:
    """Exempt from the address *rules*, not from the transport. The connection still goes
    to a resolved address with the hostname preserved, so a configured endpoint gets the
    same rebinding protection everything else does."""
    net = FakeNetwork(payload=SEARXNG_PAYLOAD)
    search_sync(
        SEARXNG,
        "q",
        endpoint="https://searx.example.com/search",
        resolve=resolver(("203.0.113.9",)),
        connect=net.connector,
    )
    (sent,) = net.sent
    assert sent.host == "searx.example.com"
    assert sent.address == "203.0.113.9"


def test_an_endpoint_that_does_not_resolve_still_attempts_the_connection() -> None:
    """Same rule as the fetcher's: an unresolvable name is not proven hostile, and the
    connection is where a name that truly does not exist fails."""

    def dead(_host: str) -> tuple[str, ...]:
        raise OSError("Name or service not known")

    net = FakeNetwork(payload=SEARXNG_PAYLOAD)
    search_sync(
        SEARXNG,
        "q",
        endpoint="https://searx.example.com/search",
        resolve=dead,
        connect=net.connector,
    )
    (sent,) = net.sent
    assert sent.address == "searx.example.com", "nothing to pin, so the name is dialled"


# --------------------------------------------------------------------------- #
# failures say what happened
# --------------------------------------------------------------------------- #


def test_a_missing_key_is_refused_before_anything_is_contacted() -> None:
    """Naming the variable, because "not configured" without the name is a message that
    sends someone to the source to find out which one."""
    with pytest.raises(SearchError, match="BRAVE_API_KEY"):
        build_request(BRAVE, "q")


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "BRAVE_API_KEY"),
        (403, "BRAVE_API_KEY"),
        (429, "rate-limiting"),
        (500, "HTTP 500"),
        (503, "HTTP 503"),
    ],
)
def test_each_failing_status_is_explained_in_its_own_terms(status: int, expected: str) -> None:
    """A 401 and a 500 need different actions from the user, so they get different
    sentences. "search failed" for both is a message that helps nobody."""
    net = FakeNetwork(payload={}, status=status)
    with pytest.raises(SearchError, match=re.escape(expected)):
        run(BRAVE, net=net)


def test_a_response_that_is_not_json_is_named_as_such() -> None:
    """An HTML error page from a proxy is the common cause, and "not JSON" points at it
    far better than a ``JSONDecodeError`` traceback would."""
    net = FakeNetwork(raw_body=b"<html><body>gateway timeout</body></html>")
    with pytest.raises(SearchError, match="not JSON"):
        run(BRAVE, net=net)


def test_json_in_an_unexpected_shape_is_named_rather_than_returned_empty() -> None:
    """The failure mode this guards is the worst of the set: a provider changes its
    schema, every search returns zero results, and nothing looks broken. "no result list
    at web.results" is a bug report; an empty list is a mystery."""
    net = FakeNetwork(payload={"error": {"code": "SUBSCRIPTION_TOKEN_INVALID"}})
    with pytest.raises(SearchError, match=re.escape("no result list at web.results")):
        run(BRAVE, net=net)


def test_a_genuinely_empty_result_list_is_not_an_error() -> None:
    """Zero results for an obscure query is an answer, and ``web_search`` already has
    good wording for it. Only a *missing* list is a failure."""
    net = FakeNetwork(payload={"web": {"results": []}})
    results, _net = run(BRAVE, net=net)
    assert results == ()


def test_a_row_with_no_url_is_dropped() -> None:
    """A result exists so ``web_fetch`` can be pointed at it. A row with no link wastes
    a line of context and invites the model to invent one."""
    net = FakeNetwork(
        payload={
            "web": {
                "results": [
                    {"title": "no link here", "description": "..."},
                    {"title": "fine", "url": "https://example.com/ok", "description": "d"},
                ]
            }
        }
    )
    results, _net = run(BRAVE, net=net)
    assert [result.url for result in results] == ["https://example.com/ok"]


def test_a_result_list_containing_junk_skips_the_junk() -> None:
    """Providers do put non-objects in arrays. A string where a result belongs must be
    skipped rather than crash the parse and lose the real results beside it."""
    rows = {
        "web": {
            "results": [
                "a bare string",
                None,
                42,
                {"title": "real", "url": "https://example.com/real", "description": "d"},
            ]
        }
    }
    assert [result.url for result in parse_results(BRAVE, rows)] == ["https://example.com/real"]


def test_an_endpoint_on_a_private_address_that_does_not_resolve_is_still_attempted() -> None:
    """The private-endpoint path with a dead resolver: two exemptions crossing. It must
    not raise on the way through — the connection is where it fails, as everywhere else.
    """

    def dead(_host: str) -> tuple[str, ...]:
        raise OSError("Name or service not known")

    net = FakeNetwork(payload=SEARXNG_PAYLOAD)
    search_sync(
        SEARXNG,
        "q",
        endpoint="http://searx.internal:8888/search",
        resolve=dead,
        connect=net.connector,
    )
    (sent,) = net.sent
    assert sent.address == "searx.internal"


def test_a_transport_failure_is_reported_with_the_endpoint_redacted() -> None:
    """A dead endpoint is the commonest searxng problem — the instance is not running —
    and the message has to say which endpoint, without leaking a key that a
    misconfiguration may have put in the URL."""

    def exploding(**_kwargs: object) -> object:
        raise OSError("Connection refused")

    with pytest.raises(SearchError, match="Connection refused") as caught:
        search_sync(
            SEARXNG,
            "q",
            endpoint="http://searx.example.com/search?token=SUPERSECRET",
            resolve=resolver(),
            connect=exploding,  # type: ignore[arg-type]
        )
    assert "SUPERSECRET" not in str(caught.value)
    assert "searx.example.com" in str(caught.value)


def test_a_row_with_no_title_falls_back_to_its_url() -> None:
    """Better than ``(untitled)``: the URL is information, and it is the thing the model
    will act on anyway."""
    rows = {"web": {"results": [{"url": "https://example.com/x", "description": "d"}]}}
    (result,) = parse_results(BRAVE, rows)
    assert result.title == "https://example.com/x"


def test_the_result_count_is_capped() -> None:
    """A provider ignoring the requested count must not put fifty rows in a tool
    result, so the cap is applied on this side too."""
    many = {"web": {"results": [{"url": f"https://e.example/{n}"} for n in range(50)]}}
    assert len(parse_results(BRAVE, many, limit=3)) == 3


def test_a_search_error_is_a_tool_error() -> None:
    """So the tool boundary turns it into ``ToolResult(ok=False)`` with the message the
    model reads, rather than "web_search failed unexpectedly"."""
    from ronin.tools.base import ToolError

    assert issubclass(SearchError, ToolError)


# --------------------------------------------------------------------------- #
# the seam the tool layer uses
# --------------------------------------------------------------------------- #


async def test_the_searcher_satisfies_the_tool_layers_contract() -> None:
    """``Searcher`` returns a sequence of mappings with the three keys ``WebSearchTool``
    renders. Returning values with the wrong keys would render ``(untitled)`` rows."""
    net = FakeNetwork(payload=BRAVE_PAYLOAD)
    search = provider_searcher(BRAVE, key="k", resolve=resolver(), connect=net.connector)
    rows = await search("ripgrep multiline")
    assert [sorted(row) for row in rows] == [["snippet", "title", "url"]] * len(rows)
    assert rows[0]["url"] == "https://example.com/rg/guide"


async def test_the_whole_tool_works_on_top_of_it(tmp_path: object) -> None:
    """End to end through ``WebSearchTool``: the real searcher, the real renderer.

    This is what the wiring in ``cli/wire.py`` makes real, and it is the first time in
    the repo's history that ``web_search`` has had anything behind it.
    """
    from harness import context

    from ronin.tools.net import WebSearchTool

    net = FakeNetwork(payload=BRAVE_PAYLOAD)
    tool = WebSearchTool(
        search=provider_searcher(BRAVE, key="k", resolve=resolver(), connect=net.connector)
    )
    result = await tool.execute({"query": "ripgrep multiline"}, context(tmp_path))  # type: ignore[arg-type]

    assert result.ok, result.error
    assert "ripgrep user guide" in result.content
    assert "https://example.com/rg/guide" in result.content
    assert "--multiline enables patterns spanning lines" in result.content
    assert "fetch the promising ones with web_fetch" in result.content


async def test_a_failing_search_reaches_the_model_as_a_result_not_a_crash(
    tmp_path: object,
) -> None:
    """``ToolResult(ok=False)`` carrying the explanation, which is this project's rule
    for every tool boundary."""
    from harness import context

    from ronin.tools.net import WebSearchTool

    net = FakeNetwork(payload={}, status=429)
    tool = WebSearchTool(
        search=provider_searcher(BRAVE, key="k", resolve=resolver(), connect=net.connector)
    )
    result = await tool.execute({"query": "q"}, context(tmp_path))  # type: ignore[arg-type]

    assert not result.ok
    assert "rate-limiting" in (result.error or "")
