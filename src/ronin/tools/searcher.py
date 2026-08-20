"""The search backends behind ``web_search``: three providers, one contract.

``web_search`` had the same shape of hole ``web_fetch`` had before
:mod:`ronin.tools.fetcher` — :data:`~ronin.tools.net.Searcher` was a type alias nothing
implemented, so the tool existed in tests and nowhere else. The reason it stayed empty
longer is that a searcher needs a *provider*, and picking one is a decision about
somebody's account and somebody's bill rather than a design question.

**Three, because they answer different questions.** :data:`BRAVE` is a general web index
with a documented JSON API and a free tier. :data:`TAVILY` is built for agents and
returns longer, cleaner snippets, so fewer results need a follow-up fetch. :data:`SEARXNG`
is a metasearch aggregator you run yourself: no key, no account, no bill, and no third
party learning what your agent searches for. They are one table rather than three
modules because the differences are entirely *data* — an endpoint, an auth header, a
response shape — and a provider whose adapter is fifteen lines of table does not earn a
module.

**What is deliberately not here: scraping.** No HTML endpoint of DuckDuckGo, Google or
Bing. It violates their terms, it breaks on any markup change, and a tool that silently
degrades to garbage when a competitor reshuffles a ``<div>`` is worse than a tool that
says it is not configured.

**The query is the only thing the model controls.** It arrives as a URL *parameter* or a
JSON field, never as part of a URL the model composed, so a model cannot point the
searcher at a different host by writing a clever query. The endpoint is configuration.

**Why the endpoint is not subject to the model-URL policy.** :mod:`ronin.safety.net`
refuses private and loopback addresses because *a URL the model chose* must not reach
inside the user's network. A configured SearXNG on ``http://localhost:8888`` is the
opposite case: the user put it there, exactly as they configure a local model server at
``localhost:11434``, and Ronin has always dialled those. So the configured endpoint is
reached without the model-URL check — while everything else about the pinned transport
still applies, and any **redirect** away from the endpoint is checked in full, because a
redirect is chosen by the server rather than by the user. The distinction is
config-versus-model, not public-versus-private, and it is the same line the rest of the
product already draws.

Built on :func:`ronin.tools.fetcher.request_once`, so the searcher inherits address
pinning, TLS verification against the hostname, the size cap and the timeout from the
one place that implements them. A searcher with its own socket code would be a searcher
that eventually forgets to pin.

Nothing here reads the environment or a config file: a provider name, a key and an
endpoint arrive as arguments, which is what keeps the module pure and its tests offline.
:func:`ronin.cli.wire.build_runtime` does the reading, because that is the layer that
owns joins.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import quote

from ..safety.net import (
    Resolution,
    Resolver,
    UrlNotAllowed,
    check_scheme,
    redact_url,
    resolve_and_check,
    split_host,
    system_resolver,
)
from .base import ToolError
from .fetcher import (
    DEFAULT_TIMEOUT_SECONDS,
    Connector,
    default_connector,
    request_once,
)
from .net import Searcher

#: Results asked for, and the most a result list may contain. Ten is what fits in a tool
#: result without crowding the window, and the tool's own description tells the model to
#: fetch the promising one or two rather than read all of them.
DEFAULT_LIMIT: Final = 10

#: A search response is smaller than a web page by orders of magnitude, so the cap is
#: much lower than the fetcher's — a JSON body past this is a provider misbehaving, and
#: reading further would only feed a bigger parse.
MAX_RESPONSE_BYTES: Final = 2_000_000


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One result, in the three fields ``web_search`` renders.

    A value rather than a bare ``dict`` so a provider adapter that forgets ``snippet``
    fails in its own tests instead of rendering ``(untitled)`` rows at a user.
    """

    title: str
    url: str
    snippet: str = ""

    def as_row(self) -> dict[str, str]:
        """The mapping :data:`~ronin.tools.net.Searcher` is contracted to return."""
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


@dataclass(frozen=True, slots=True)
class Provider:
    """Everything that differs between one search API and the next.

    ``key_env`` is the *name* of the variable holding the credential, not the credential
    — the convention ``.env.example`` describes for models, applied here so adding a
    provider stays a table entry rather than a patch to the reader.
    """

    name: str
    endpoint: str
    #: ``""`` for a provider that needs no credential (a SearXNG you run yourself).
    key_env: str = ""
    #: ``POST`` providers send the query as JSON; ``GET`` ones as a query parameter.
    method: str = "GET"
    #: Header carrying the credential, and the format string for its value.
    auth_header: str = ""
    auth_format: str = "{key}"
    #: Where the results live in the response, tried in order — the first list wins.
    result_paths: tuple[tuple[str, ...], ...] = (("results",),)
    #: Keys to read each field from, tried in order, so one adapter covers small
    #: differences in field naming without a per-provider parse function.
    title_keys: tuple[str, ...] = ("title",)
    url_keys: tuple[str, ...] = ("url",)
    snippet_keys: tuple[str, ...] = ("description", "content", "snippet")
    #: Extra query parameters (GET) or body fields (POST), beyond the query itself.
    extra: Mapping[str, str] = field(default_factory=dict)
    #: Name of the query parameter or JSON field carrying the search terms.
    query_field: str = "q"
    #: Name of the field carrying the result count, if the provider takes one.
    limit_field: str = "count"

    @property
    def needs_key(self) -> bool:
        return bool(self.key_env)


#: Brave's Web Search API. A documented JSON endpoint with a free tier, one
#: ``X-Subscription-Token`` header, and results under ``web.results``.
BRAVE: Final = Provider(
    name="brave",
    endpoint="https://api.search.brave.com/res/v1/web/search",
    key_env="BRAVE_API_KEY",
    auth_header="X-Subscription-Token",
    result_paths=(("web", "results"),),
    snippet_keys=("description",),
    extra={"result_filter": "web"},
)

#: Tavily, built for agents: a POST of JSON, a bearer token, and longer snippets under
#: ``content``. ``search_depth=basic`` because ``advanced`` costs more credits per call
#: and the tool's job is to find a page for ``web_fetch``, not to answer from snippets.
TAVILY: Final = Provider(
    name="tavily",
    endpoint="https://api.tavily.com/search",
    key_env="TAVILY_API_KEY",
    method="POST",
    auth_header="Authorization",
    auth_format="Bearer {key}",
    result_paths=(("results",),),
    snippet_keys=("content",),
    extra={"search_depth": "basic"},
    query_field="query",
    limit_field="max_results",
)

#: SearXNG, self-hosted. No credential, and ``endpoint`` is expected to be overridden
#: with the instance's own URL — the default is the shape, not a service anyone runs for
#: you. ``format=json`` has to be enabled in the instance's settings.
SEARXNG: Final = Provider(
    name="searxng",
    endpoint="http://localhost:8888/search",
    result_paths=(("results",),),
    snippet_keys=("content",),
    extra={"format": "json"},
    #: SearXNG returns everything it has; the count is applied on our side.
    limit_field="",
)

PROVIDERS: Final[Mapping[str, Provider]] = {
    provider.name: provider for provider in (BRAVE, TAVILY, SEARXNG)
}


class SearchError(ToolError):
    """A search that could not be performed. A :class:`ToolError`, so the model reads it.

    Subclassed rather than raising ``ToolError`` directly so the wiring layer can tell
    "this search failed" from "this tool refused an argument" when deciding whether to
    keep offering the tool.
    """


def provider_named(name: str) -> Provider:
    """The provider called ``name``, or a refusal naming the ones that exist."""
    found = PROVIDERS.get(name.strip().lower())
    if found is None:
        raise SearchError(
            f"no search provider named {name!r}. Configured with RONIN_SEARCH_PROVIDER; "
            f"the ones this build knows are: {', '.join(sorted(PROVIDERS))}."
        )
    return found


def _dig(payload: Any, path: Sequence[str]) -> Any:
    """Walk a dotted path through decoded JSON, returning ``None`` at the first miss."""
    current = payload
    for step in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(step)
    return current


def _first(row: Mapping[str, Any], keys: Sequence[str]) -> str:
    """The first key present and stringable, else ``""``."""
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def parse_results(
    provider: Provider, payload: Any, *, limit: int = DEFAULT_LIMIT
) -> tuple[SearchResult, ...]:
    """Decoded JSON to results, tolerating everything except a shape with no results.

    A row with no URL is dropped rather than rendered: the whole point of a result is
    that ``web_fetch`` can be pointed at it, and a row the model cannot follow is a row
    that wastes a line of context and invites a hallucinated link.
    """
    rows: Any = None
    for path in provider.result_paths:
        rows = _dig(payload, path)
        if isinstance(rows, list):
            break
    if not isinstance(rows, list):
        where = " or ".join(".".join(path) for path in provider.result_paths)
        raise SearchError(
            f"{provider.name} returned JSON with no result list at {where}. The API "
            f"shape may have changed, or the response may be an error envelope this "
            f"build does not recognise."
        )
    found: list[SearchResult] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        url = _first(row, provider.url_keys)
        if not url:
            continue
        found.append(
            SearchResult(
                title=_first(row, provider.title_keys) or url,
                url=url,
                snippet=_first(row, provider.snippet_keys),
            )
        )
        if len(found) >= limit:
            break
    return tuple(found)


def build_request(
    provider: Provider,
    query: str,
    *,
    key: str = "",
    endpoint: str = "",
    limit: int = DEFAULT_LIMIT,
) -> tuple[str, dict[str, str], bytes | None]:
    """``(url, headers, body)`` for one search. Pure, so the wire format is testable.

    The query is percent-encoded into a parameter or placed in a JSON field — never
    concatenated into the host or path — so no query can move the request to another
    server. That is the reason this function exists separately from the one that sends
    it: the encoding is a security property, not a formatting detail.
    """
    if provider.needs_key and not key:
        raise SearchError(
            f"web_search is configured for {provider.name}, but {provider.key_env} is "
            f"not set, so there is no credential to search with."
        )
    target = (endpoint or provider.endpoint).rstrip("?&")
    headers: dict[str, str] = {"Accept": "application/json"}
    if provider.auth_header and key:
        headers[provider.auth_header] = provider.auth_format.format(key=key)

    if provider.method == "POST":
        fields: dict[str, Any] = {provider.query_field: query, **provider.extra}
        if provider.limit_field:
            fields[provider.limit_field] = limit
        headers["Content-Type"] = "application/json"
        return target, headers, json.dumps(fields).encode()

    parts = [f"{provider.query_field}={quote(query, safe='')}"]
    parts.extend(f"{name}={quote(value, safe='')}" for name, value in provider.extra.items())
    if provider.limit_field:
        parts.append(f"{provider.limit_field}={int(limit)}")
    joiner = "&" if "?" in target else "?"
    return f"{target}{joiner}{'&'.join(parts)}", headers, None


def _endpoint_resolution(url: str, resolve: Resolver) -> Resolution:
    """Vet a *configured* endpoint: reachable, pinnable, but private is allowed.

    The model-URL policy is deliberately not applied here — see the module docstring for
    why a configured endpoint is the same kind of thing as a configured model server.
    What is still applied: the scheme check, so a typo cannot turn the endpoint into a
    ``file://`` read, and resolution, so the connection can still be pinned to the
    address that was vetted rather than re-resolving at connect time.
    """
    host = split_host(url).lower()
    try:
        return resolve_and_check(url, resolve=resolve)
    except UrlNotAllowed:
        # A private or loopback endpoint: allowed *because it is configuration*, and
        # pinned to whatever it resolves to so the rest of the transport is unchanged.
        try:
            addresses = tuple(resolve(host))
        except OSError:
            return Resolution(host=host, resolved=False, note="the endpoint did not resolve")
        return Resolution(host=host, addresses=addresses)


def search_sync(
    provider: Provider,
    query: str,
    *,
    key: str = "",
    endpoint: str = "",
    limit: int = DEFAULT_LIMIT,
    resolve: Resolver = system_resolver,
    connect: Connector = default_connector,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> tuple[SearchResult, ...]:
    """One search, blocking. Every seam injected so this is testable with no network."""
    url, headers, body = build_request(provider, query, key=key, endpoint=endpoint, limit=limit)
    # The scheme rule still applies to a configured endpoint — `file://` or `gopher://`
    # in a config value is a mistake worth naming, not a protocol to attempt — and it is
    # applied through `safety.net` rather than re-implemented here. The *address* rule is
    # what a configured endpoint is exempt from; see this module's docstring.
    try:
        check_scheme(url)
        vetted = _endpoint_resolution(url, resolve)
    except UrlNotAllowed as exc:
        raise SearchError(f"the configured {provider.name} endpoint is unusable: {exc}") from exc

    try:
        response = request_once(
            url,
            vetted,
            connect=connect,
            timeout=timeout,
            max_bytes=max_bytes,
            method=provider.method,
            headers=headers,
            body=body,
        )
    except OSError as exc:
        raise SearchError(
            f"could not reach the {provider.name} search endpoint "
            f"({redact_url(url)!r}): {type(exc).__name__}: {exc}"
        ) from exc

    if response.status == 401 or response.status == 403:
        credential = provider.key_env or "the instance's access settings"
        raise SearchError(
            f"{provider.name} rejected the request (HTTP {response.status}). Check "
            f"{credential}; a key that has expired or lacks this endpoint's scope looks "
            f"exactly like this."
        )
    if response.status == 429:
        raise SearchError(
            f"{provider.name} is rate-limiting this key (HTTP 429). The search did not "
            f"run; waiting and retrying is the only fix from here."
        )
    if not 200 <= response.status < 300:
        raise SearchError(
            f"{provider.name} answered HTTP {response.status} for a search. The query was not run."
        )
    try:
        payload = json.loads(response.body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        hint = " (the response was truncated at the size cap)" if response.truncated else ""
        raise SearchError(
            f"{provider.name} returned something that is not JSON{hint}: {exc}"
        ) from exc
    return parse_results(provider, payload, limit=limit)


def provider_searcher(
    provider: Provider,
    *,
    key: str = "",
    endpoint: str = "",
    limit: int = DEFAULT_LIMIT,
    resolve: Resolver = system_resolver,
    connect: Connector = default_connector,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> Searcher:
    """A :data:`~ronin.tools.net.Searcher` for one configured provider.

    The blocking request runs in a worker thread, as the fetcher's does, so one search
    does not stall the event loop the agent is streaming on.
    """

    async def search(query: str) -> Sequence[Mapping[str, str]]:
        results = await asyncio.to_thread(
            search_sync,
            provider,
            query,
            key=key,
            endpoint=endpoint,
            limit=limit,
            resolve=resolve,
            connect=connect,
            timeout=timeout,
            max_bytes=max_bytes,
        )
        return [result.as_row() for result in results]

    return search


__all__ = [
    "BRAVE",
    "DEFAULT_LIMIT",
    "MAX_RESPONSE_BYTES",
    "PROVIDERS",
    "SEARXNG",
    "TAVILY",
    "Provider",
    "SearchError",
    "SearchResult",
    "build_request",
    "parse_results",
    "provider_named",
    "provider_searcher",
    "search_sync",
]
