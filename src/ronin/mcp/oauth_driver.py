"""OAuth 2.1 live driver: the impure half the pure core in :mod:`ronin.mcp.oauth` refuses.

:mod:`ronin.mcp.oauth` is transforms — ``(endpoint, form) -> TokenSet``, ``now`` passed in,
no socket ever opened. This module is the orchestration that strings those transforms into
a real flow, plus the three impure edges the flow needs and cannot fake:

* an **HTTPS client** to GET metadata and POST registration/token requests
  (:class:`HttpFetcher`);
* a **browser + loopback redirect capture** to run user consent (:class:`Authorizer`);
* a **place to persist** the issued client identity and tokens across runs
  (:class:`AuthStore`).

Every edge is an injected Protocol, so the whole ``discover -> register -> authorize ->
exchange -> refresh`` sequence is driven offline in tests with fakes — the same discipline
:mod:`ronin.cli.detect` uses. Only the leaf adapters at the bottom of this file
(:class:`HttpxFetcher`, :class:`LoopbackAuthorizer`, :class:`FileAuthStore`) ever touch a
socket, a browser, or the disk, and — exactly like :class:`ronin.mcp.transport.HttpxSender`
— they are lazy-imported and out of the offline suite's reach.

Fail-closed choices, continued from the core:

* **Startup never launches a browser.** :meth:`OAuthDriver.ensure` with ``interactive=False``
  (the session-startup default) will use a stored token, or refresh one, but will *not*
  begin an interactive authorization — a headless or remote session would otherwise hang on
  a browser that never opens. A missing token becomes a named failure telling the operator
  to log in, and the session continues on its other tools.
* **The randomness and the clock are injected.** ``new_verifier`` / ``new_state`` are the
  only source of PKCE and CSRF entropy and ``now`` the only clock; the real driver wires
  them to :mod:`secrets` and :func:`time.time`, and a test wires them to fixtures so a flow
  is byte-for-byte reproducible.
* **A token is never written to a plaintext file.** :class:`KeyringAuthStore` keeps it in
  the OS keyring, which encrypts at rest; :class:`FileAuthStore` (the fallback when no
  keyring backend exists) persists only the non-secret ``client_id`` and keeps the token in
  a session-only in-memory overlay. Either way, a bearer credential never lands in the clear.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from .config import AuthKind, McpServerConfig
from .oauth import (
    AuthServerMetadata,
    ClientRegistration,
    OAuthError,
    ProtectedResourceMetadata,
    TokenRequest,
    TokenSet,
    authorization_code_request,
    authorization_url,
    pkce_from_verifier,
    refresh_request,
    registration_request,
)

#: Where the loopback redirect listens by default. A fixed loopback port keeps the
#: redirect URI stable across runs, which matters because a dynamically registered
#: client pins its ``redirect_uris`` and a changing port would invalidate them.
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8471/callback"

#: The two well-known suffixes an authorization server may publish its metadata under.
#: RFC 8414 defines the first; many servers only expose the OpenID one, so it is tried
#: as a fallback rather than treated as absent.
_AS_METADATA_SUFFIXES = ("oauth-authorization-server", "openid-configuration")

#: RFC 9728 §3 — a protected resource publishes its metadata here.
_PR_METADATA_SUFFIX = "oauth-protected-resource"


# --------------------------------------------------------------------------- #
# The impure edges, as protocols
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class HttpReply:
    """One HTTP response, reduced to what the flow reads: status and a decoded body.

    ``body`` is the raw bytes; :meth:`json` parses it lazily so a non-2xx reply can be
    reported with its text without first failing a JSON parse on an HTML error page.
    """

    status: int
    body: bytes

    def json(self, *, what: str) -> Mapping[str, object]:
        try:
            data = json.loads(self.body or b"{}")
        except ValueError as exc:
            raise OAuthError(f"{what}: response was not JSON ({exc})") from exc
        if not isinstance(data, Mapping):
            raise OAuthError(f"{what}: response was not a JSON object")
        return data

    def text(self, *, limit: int = 300) -> str:
        return self.body[:limit].decode("utf-8", errors="replace")


class HttpFetcher:
    """GET/POST for the OAuth endpoints. The real one is :class:`HttpxFetcher`.

    Three narrow methods rather than one general ``request`` because each maps to a fixed
    content type the flow depends on: metadata is ``GET``, registration is a JSON body,
    and the token endpoint is ``application/x-www-form-urlencoded`` (RFC 6749 §4.1.3). A
    single method taking a free-form content type is one typo away from posting a token
    request as JSON, which every authorization server rejects with an opaque 400.
    """

    async def get(self, url: str) -> HttpReply:
        raise NotImplementedError

    async def post_form(self, url: str, form: Mapping[str, str]) -> HttpReply:
        raise NotImplementedError

    async def post_json(self, url: str, body: Mapping[str, object]) -> HttpReply:
        raise NotImplementedError


class Authorizer:
    """Drive user consent and return the authorization code. Real one: :class:`LoopbackAuthorizer`.

    The implementation opens ``authorization_url`` in a browser, catches the redirect to
    ``redirect_uri`` on a loopback listener, checks ``state`` and surfaces any ``error=``
    (via :func:`ronin.mcp.oauth.parse_redirect`), and returns the bare code. It is the one
    seam that blocks on a human, which is why the driver only calls it when the caller has
    said the session is interactive.
    """

    async def authorize(self, url: str, *, redirect_uri: str, expected_state: str) -> str:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class PersistedAuth:
    """What a store holds for one server: its client identity and last token set.

    The client identity is durable because dynamic registration (RFC 7591) is meant to
    happen *once* — re-registering on every launch litters the authorization server with
    clients and, on servers that rate-limit registration, eventually fails. The token is the
    sensitive half: a live bearer credential, kept so a still-valid access token is reused
    and an expiring one refreshed without a new browser round trip. How long each half
    survives is the store's choice — :class:`FileAuthStore` keeps only the ``client_id`` on
    disk (a token must never be written in clear text), while :class:`KeyringAuthStore` keeps
    both in the OS keyring, which encrypts at rest.

    :meth:`to_json` / :meth:`from_json` are the serialisation a keyring value round-trips
    through; they are deliberately *not* used by :class:`FileAuthStore`, which persists a bare
    ``client_id`` precisely so no token text reaches a plaintext file.
    """

    client_id: str = ""
    client_secret: str = ""
    token: TokenSet | None = None

    def to_json(self) -> Mapping[str, object]:
        out: dict[str, object] = {"client_id": self.client_id}
        if self.client_secret:
            out["client_secret"] = self.client_secret
        if self.token is not None:
            out["token"] = {
                "access_token": self.token.access_token,
                "resource": self.token.resource,
                "refresh_token": self.token.refresh_token,
                "expires_at": self.token.expires_at,
                "scopes": list(self.token.scopes),
            }
        return out

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> PersistedAuth:
        raw = data.get("token")
        token: TokenSet | None = None
        if isinstance(raw, Mapping):
            access = raw.get("access_token")
            resource = raw.get("resource")
            if isinstance(access, str) and access and isinstance(resource, str) and resource:
                expires = raw.get("expires_at")
                scopes = raw.get("scopes")
                token = TokenSet(
                    access_token=access,
                    resource=resource,
                    refresh_token=_as_str(raw.get("refresh_token")),
                    expires_at=float(expires) if isinstance(expires, (int, float)) else None,
                    scopes=tuple(s for s in scopes if isinstance(s, str))
                    if isinstance(scopes, Sequence) and not isinstance(scopes, (str, bytes))
                    else (),
                )
        return cls(
            client_id=_as_str(data.get("client_id")),
            client_secret=_as_str(data.get("client_secret")),
            token=token,
        )


class AuthStore:
    """Persist :class:`PersistedAuth` per server key. Real one: :class:`FileAuthStore`.

    ``load`` returns ``None`` for a server never authorized — a first run is not an error.
    An in-memory implementation is trivial, which is what the tests use.
    """

    def load(self, key: str) -> PersistedAuth | None:
        raise NotImplementedError

    def save(self, key: str, state: PersistedAuth) -> None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# The driver
# --------------------------------------------------------------------------- #


class OAuthDriver:
    """Turns a server config into a bearer token, running as much of the flow as it can.

    Composed of the three injected edges plus the injected entropy/clock. It holds no
    mutable state of its own: every persisted fact goes through :class:`AuthStore`, so two
    drivers sharing a store see each other's tokens and a driver is safe to reconstruct per
    call.
    """

    def __init__(
        self,
        *,
        fetcher: HttpFetcher,
        store: AuthStore,
        now: Callable[[], float],
        new_verifier: Callable[[], str],
        new_state: Callable[[], str],
        authorizer: Authorizer | None = None,
        client_name: str = "ronin",
        redirect_uri: str = DEFAULT_REDIRECT_URI,
        scopes: Sequence[str] = (),
    ) -> None:
        self._fetcher = fetcher
        self._store = store
        self._now = now
        self._new_verifier = new_verifier
        self._new_state = new_state
        self._authorizer = authorizer
        self._client_name = client_name
        self._redirect_uri = redirect_uri
        self._scopes = tuple(scopes)

    # ------------------------------------------------------------- discovery --

    async def discover(
        self, resource_url: str, *, resource_metadata_url: str | None = None
    ) -> tuple[ProtectedResourceMetadata, AuthServerMetadata]:
        """Resolve the protected-resource and authorization-server metadata for ``resource_url``.

        ``resource_metadata_url`` is the authoritative pointer a ``401`` hands back in its
        ``WWW-Authenticate`` header; absent that, the RFC 9728 well-known path derived from
        the server URL is used, so discovery works from a bare configured URL too.
        """
        prm_url = resource_metadata_url or _well_known(resource_url, _PR_METADATA_SUFFIX)
        prm = ProtectedResourceMetadata.from_json(
            (await self._get(prm_url, what="protected-resource metadata")).json(
                what="protected-resource metadata"
            )
        )
        issuer = prm.authorization_servers[0]
        return prm, await self._auth_server_metadata(issuer)

    async def _auth_server_metadata(self, issuer: str) -> AuthServerMetadata:
        last = ""
        for suffix in _AS_METADATA_SUFFIXES:
            url = _well_known(issuer, suffix)
            reply = await self._fetcher.get(url)
            if reply.status == 200:
                return AuthServerMetadata.from_json(
                    reply.json(what="authorization-server metadata")
                )
            last = f"{url} -> {reply.status}"
        raise OAuthError(
            f"authorization server {issuer!r} published no usable metadata "
            f"(tried {list(_AS_METADATA_SUFFIXES)}; last: {last})"
        )

    # ------------------------------------------------------------ the flow ----

    async def ensure(self, config: McpServerConfig, *, interactive: bool = False) -> TokenSet:
        """Return a usable token for ``config``: reuse, refresh, or (only if allowed) obtain.

        The decision tree, in order: a stored token that is still fresh is returned as-is; a
        stored-but-expiring token with a refresh token is refreshed; otherwise, only when
        ``interactive`` is true, a full browser authorization is run. A non-interactive call
        with nothing usable raises — startup turns that into a "log in first" failure rather
        than a hang.
        """
        self._require_oauth(config)
        state = self._store.load(config.name)
        token = state.token if state is not None else None
        if token is not None and not token.is_expired(self._now()):
            return token
        if token is not None and token.refresh_token:
            return await self.refresh(config, token, state=state)
        if interactive:
            return await self.obtain(config, state=state)
        raise OAuthError(
            f"no usable OAuth token for server {config.name!r}. Run an interactive login "
            "(a session that can open a browser) to authorize it; startup will not open one."
        )

    async def obtain(
        self, config: McpServerConfig, *, state: PersistedAuth | None = None
    ) -> TokenSet:
        """Run the full authorization-code + PKCE flow and persist the result.

        Requires an :class:`Authorizer`; without one there is no way to complete consent and
        the call fails closed rather than blocking. Reuses a previously registered client id
        when the store has one, registering dynamically only on the first authorization.
        """
        self._require_oauth(config)
        if self._authorizer is None:
            raise OAuthError(
                f"cannot authorize server {config.name!r}: obtaining a new token needs an "
                "interactive authorizer (a browser), and none was provided to the driver"
            )
        state = state if state is not None else self._store.load(config.name)
        prm, asm = await self.discover(config.url)
        client_id, client_secret = await self._client_identity(asm, state)
        scopes = self._scopes or prm.scopes_supported
        pkce = pkce_from_verifier(self._new_verifier())
        csrf = self._new_state()
        url = authorization_url(
            asm,
            client_id=client_id,
            redirect_uri=self._redirect_uri,
            pkce=pkce,
            state=csrf,
            resource=prm.resource,
            scopes=scopes,
        )
        code = await self._authorizer.authorize(
            url, redirect_uri=self._redirect_uri, expected_state=csrf
        )
        token = await self._token(
            authorization_code_request(
                asm,
                client_id=client_id,
                code=code,
                pkce=pkce,
                redirect_uri=self._redirect_uri,
                resource=prm.resource,
                client_secret=client_secret,
            ),
            resource=prm.resource,
        )
        self._store.save(
            config.name,
            PersistedAuth(client_id=client_id, client_secret=client_secret, token=token),
        )
        return token

    async def refresh(
        self, config: McpServerConfig, token: TokenSet, *, state: PersistedAuth | None = None
    ) -> TokenSet:
        """Trade the refresh token for a fresh access token (same resource) and persist it."""
        self._require_oauth(config)
        if not token.refresh_token:
            raise OAuthError(
                f"server {config.name!r}: token has no refresh_token; a fresh authorization "
                "is required"
            )
        state = state if state is not None else self._store.load(config.name)
        client_id = state.client_id if state is not None else ""
        if not client_id:
            raise OAuthError(
                f"server {config.name!r}: no registered client_id on record to refresh with; "
                "authorize again"
            )
        client_secret = state.client_secret if state is not None else ""
        _, asm = await self.discover(config.url)
        fresh = await self._token(
            refresh_request(
                asm,
                client_id=client_id,
                refresh_token=token.refresh_token,
                resource=token.resource,
                client_secret=client_secret,
                scopes=self._scopes,
            ),
            resource=token.resource,
            fallback_refresh_token=token.refresh_token,
        )
        self._store.save(
            config.name,
            PersistedAuth(client_id=client_id, client_secret=client_secret, token=fresh),
        )
        return fresh

    async def force_refresh(self, config: McpServerConfig) -> TokenSet:
        """Refresh the stored token now, regardless of its clock expiry.

        This is the mid-session ``401`` path: the server rejected the bearer even though our
        own clock may still think it is fresh (a revocation, or clock skew), so :meth:`ensure`
        — which would hand back the not-yet-expired cached token unchanged — is wrong here.
        Loads the stored token and drives :meth:`refresh` unconditionally; raises if there is
        nothing to refresh with, which the caller turns into "re-authorize".
        """
        self._require_oauth(config)
        state = self._store.load(config.name)
        token = state.token if state is not None else None
        if token is None or not token.refresh_token:
            raise OAuthError(
                f"server {config.name!r}: nothing to refresh with (no stored refresh token); "
                "an interactive re-authorization is required"
            )
        return await self.refresh(config, token, state=state)

    # ----------------------------------------------------------- internals ----

    async def _client_identity(
        self, asm: AuthServerMetadata, state: PersistedAuth | None
    ) -> tuple[str, str]:
        if state is not None and state.client_id:
            return state.client_id, state.client_secret
        if not asm.registration_endpoint:
            raise OAuthError(
                "no client_id on record and the authorization server offers no dynamic "
                "registration endpoint; register a client manually and configure its id"
            )
        reply = await self._fetcher.post_json(
            asm.registration_endpoint,
            registration_request(
                client_name=self._client_name,
                redirect_uris=[self._redirect_uri],
                scopes=self._scopes,
            ),
        )
        if reply.status not in (200, 201):
            raise OAuthError(f"dynamic client registration failed: {reply.status} {reply.text()!r}")
        reg = ClientRegistration.from_json(reply.json(what="client registration"))
        return reg.client_id, reg.client_secret

    async def _token(
        self, request: TokenRequest, *, resource: str, fallback_refresh_token: str = ""
    ) -> TokenSet:
        reply = await self._fetcher.post_form(request.url, request.form)
        if reply.status != 200:
            raise OAuthError(f"token endpoint returned {reply.status}: {reply.text()!r}")
        return TokenSet.from_response(
            reply.json(what="token response"),
            now=self._now(),
            resource=resource,
            fallback_refresh_token=fallback_refresh_token,
        )

    async def _get(self, url: str, *, what: str) -> HttpReply:
        reply = await self._fetcher.get(url)
        if reply.status != 200:
            raise OAuthError(f"{what}: GET {url} returned {reply.status}")
        return reply

    @staticmethod
    def _require_oauth(config: McpServerConfig) -> None:
        if config.auth is not AuthKind.OAUTH:
            raise OAuthError(
                f"server {config.name!r} is not configured for OAuth (auth={config.auth.value!r})"
            )


# --------------------------------------------------------------------------- #
# Wiring: resolve headers for every OAuth server, without ending the session
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ResolvedAuth:
    """The outcome of resolving OAuth for a set of servers, split into wins and losses.

    ``headers`` is what to merge into each server's transport; ``failures`` is a message per
    server that could not be authorized. The split mirrors :class:`ronin.mcp.client.McpToolset`:
    a server that needs a login is *recorded*, never raised, so it costs the session nothing
    but its own tools.
    """

    headers: Mapping[str, Mapping[str, str]]
    failures: Mapping[str, str]


def token_refresher(
    driver: OAuthDriver, config: McpServerConfig
) -> Callable[[], Awaitable[Mapping[str, str] | None]]:
    """The no-arg async hook the client fires on a mid-session ``401``.

    It force-refreshes the token and returns the fresh ``Authorization`` header, or ``None``
    when the token cannot be refreshed (no refresh token, or the refresh itself failed) — the
    client turns ``None`` into a failed call telling the model to re-authorize, never a hang
    or a silent retry loop.
    """

    async def refresh() -> Mapping[str, str] | None:
        try:
            token = await driver.force_refresh(config)
        except (OAuthError, OSError):
            return None
        return token.authorization_header()

    return refresh


async def authorize_servers(
    configs: Sequence[McpServerConfig], driver: OAuthDriver, *, interactive: bool = False
) -> ResolvedAuth:
    """Resolve a bearer header for every ``auth: oauth`` server, recording those that fail.

    Non-OAuth servers are ignored (they carry their own headers, if any). Each OAuth server
    is resolved independently: one that has no stored token, or whose refresh fails, becomes
    a ``failures`` entry and the rest still get their headers.
    """
    headers: dict[str, Mapping[str, str]] = {}
    failures: dict[str, str] = {}
    for config in configs:
        if config.auth is not AuthKind.OAUTH:
            continue
        try:
            token = await driver.ensure(config, interactive=interactive)
        except (OAuthError, OSError) as exc:
            failures[config.name] = str(exc)
            continue
        headers[config.name] = token.authorization_header()
    return ResolvedAuth(headers=headers, failures=failures)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _well_known(url: str, suffix: str) -> str:
    """The RFC 8414 / RFC 9728 well-known URL for ``url`` under ``suffix``.

    The suffix is inserted after the authority and *before* any path component, so an issuer
    with a tenant path (``https://host/tenant``) resolves to
    ``https://host/.well-known/<suffix>/tenant`` rather than dropping the tenant.
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    well_known = f"/.well-known/{suffix}"
    new_path = f"{well_known}/{path}" if path else well_known
    return urlunparse((parsed.scheme, parsed.netloc, new_path, "", "", ""))


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


# --------------------------------------------------------------------------- #
# The leaf adapters: the only code here that touches a socket, a browser, or disk
# --------------------------------------------------------------------------- #


class HttpxFetcher(HttpFetcher):
    """The real :class:`HttpFetcher`, over ``httpx``. Not exercised by the offline suite.

    ``httpx`` is imported inside each method so a bare install stays importable and the tests
    — which never construct this — need nothing. Mirrors
    :class:`ronin.mcp.transport.HttpxSender`, deliberately: the ``$0``/offline rule forbids a
    test that opens a socket, so this class is verified by hand against a real server, not in CI.
    """

    def __init__(self, *, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def get(self, url: str) -> HttpReply:  # pragma: no cover - touches the network
        return await self._request("GET", url)

    async def post_form(
        self, url: str, form: Mapping[str, str]
    ) -> HttpReply:  # pragma: no cover - touches the network
        return await self._request("POST", url, data=dict(form))

    async def post_json(
        self, url: str, body: Mapping[str, object]
    ) -> HttpReply:  # pragma: no cover - touches the network
        return await self._request("POST", url, json_body=dict(body))

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> HttpReply:  # pragma: no cover - touches the network
        try:
            import httpx
        except ImportError as exc:
            raise OAuthError(
                "httpx is required for the OAuth live driver; it is not needed by the "
                "protocol core or the offline test suite"
            ) from exc
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.request(method, url, data=data, json=json_body)
            return HttpReply(status=resp.status_code, body=resp.content)


class InMemoryAuthStore(AuthStore):
    """A dict-backed :class:`AuthStore`. The store the tests inject, and a usable default
    for an ephemeral session that should not write tokens to disk at all."""

    def __init__(self) -> None:
        self._by_key: dict[str, PersistedAuth] = {}

    def load(self, key: str) -> PersistedAuth | None:
        return self._by_key.get(key)

    def save(self, key: str, state: PersistedAuth) -> None:
        self._by_key[key] = state

    def delete(self, key: str) -> None:
        self._by_key.pop(key, None)


class FileAuthStore(AuthStore):
    """Persists only the *non-secret* ``client_id`` to disk; tokens stay in memory.

    An access/refresh token is a live bearer credential, and writing one to a plaintext file
    is a clear-text-storage risk — Ronin ships no keyring to encrypt it, so it is not written
    to disk at all. What *is* durable is the dynamically-registered ``client_id`` (a public
    PKCE client keeps no secret, RFC 7591): persisting it to ``<dir>/<server>.json`` means a
    client is registered once instead of on every launch. Tokens live in a per-process
    overlay, so within a session an access token is reused and refreshed, but a new session
    starts unauthenticated and logs in again. Durable, encrypted-at-rest token persistence
    (an OS keyring) is a deliberate follow-up, not a plaintext file.

    Unlike the other leaf adapters this one touches no network or browser — only ``tmp_path``
    — so it is exercised directly by the offline suite.
    """

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._tokens: dict[str, TokenSet] = {}

    def _path(self, key: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in key)
        return self._dir / f"{safe}.json"

    def load(self, key: str) -> PersistedAuth | None:
        client_id = ""
        path = self._path(key)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if isinstance(data, Mapping):
                client_id = _as_str(data.get("client_id"))
        token = self._tokens.get(key)
        if not client_id and token is None:
            return None
        return PersistedAuth(client_id=client_id, token=token)

    def save(self, key: str, state: PersistedAuth) -> None:
        if state.token is not None:
            self._tokens[key] = state.token
        if state.client_id:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(json.dumps({"client_id": state.client_id}), encoding="utf-8")

    def delete(self, key: str) -> None:
        self._tokens.pop(key, None)
        with contextlib.suppress(OSError):
            self._path(key).unlink()


#: Sentinel: "detect a keyring backend" vs an explicitly injected one (a fake, or ``None``).
_AUTODETECT: Any = object()

#: The keyring service name Ronin's MCP tokens live under. One namespace, keyed per server.
_KEYRING_SERVICE = "ronin-mcp-oauth"


def _load_keyring() -> Any:  # pragma: no cover - depends on the host's keyring backend
    """The ``keyring`` module if a *real* backend is present, else ``None``.

    ``keyring`` falls back to a ``fail.Keyring`` backend when the platform has no secret
    store (a headless Linux box with no Secret Service, say); that backend raises on every
    call, so it is treated as "no keyring" here and the caller degrades to memory.
    """
    try:
        import keyring  # type: ignore[import-not-found]
        from keyring.backends import fail  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
    except Exception:  # any backend-probe failure means "unusable" — degrade to memory
        return None
    return keyring


class KeyringAuthStore(AuthStore):
    """Persists client id **and** token in the OS keyring, which encrypts at rest.

    This is the store that makes ``ronin mcp login`` useful across processes: unlike
    :class:`FileAuthStore` — which refuses to write a token to a plaintext file — the platform
    keyring (macOS Keychain, Windows Credential Locker, the Secret Service on Linux) is an
    encrypted store, so a refresh token kept there is not clear-text storage and *can* be
    carried from a login to a later session.

    Degrades honestly: if no keyring backend is available (``keyring`` not installed, or a
    headless host with no secret service) it falls back to an in-memory dict, so a session
    still works within its own lifetime and a login simply does not persist past the process.
    :attr:`durable` says which mode it is in. The backend is injectable so the store's logic
    is tested offline against a fake keyring, never the developer's real one.
    """

    def __init__(self, backend: Any = _AUTODETECT) -> None:
        self._memory: dict[str, PersistedAuth] = {}
        self._keyring = _load_keyring() if backend is _AUTODETECT else backend

    @property
    def durable(self) -> bool:
        """True when a real keyring backs this store (a token will survive the process)."""
        return self._keyring is not None

    def load(self, key: str) -> PersistedAuth | None:
        if self._keyring is None:
            return self._memory.get(key)
        try:
            raw = self._keyring.get_password(_KEYRING_SERVICE, key)
        except self._keyring.errors.KeyringError:
            return self._memory.get(key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return PersistedAuth.from_json(data) if isinstance(data, Mapping) else None

    def save(self, key: str, state: PersistedAuth) -> None:
        if self._keyring is None:
            self._memory[key] = state
            return
        try:
            self._keyring.set_password(_KEYRING_SERVICE, key, json.dumps(state.to_json()))
        except self._keyring.errors.KeyringError:
            self._memory[key] = state  # a backend that fails a write still leaves a usable session

    def delete(self, key: str) -> None:
        self._memory.pop(key, None)
        if self._keyring is not None:
            with contextlib.suppress(self._keyring.errors.KeyringError):
                self._keyring.delete_password(_KEYRING_SERVICE, key)


class LoopbackAuthorizer(Authorizer):
    """The real :class:`Authorizer`: open a browser, catch the loopback redirect, return the code.

    Runs a one-shot HTTP server bound to the redirect URI's loopback host/port, opens the
    authorization URL in the user's browser, waits for the single redirect, hands its query
    string to :func:`ronin.mcp.oauth.parse_redirect` (which enforces the ``state`` check and
    surfaces a server ``error=``), and returns the bare code. Not exercised offline — it
    binds a socket and launches a browser — so it is kept to the thinnest possible glue over
    the pure parser.
    """

    def __init__(
        self, *, open_browser: Callable[[str], object] | None = None, timeout: float = 300.0
    ) -> None:
        self._open_browser = open_browser
        self._timeout = timeout

    async def authorize(
        self, url: str, *, redirect_uri: str, expected_state: str
    ) -> str:  # pragma: no cover - opens a socket and a browser
        import asyncio
        import webbrowser
        from http.server import BaseHTTPRequestHandler, HTTPServer

        from .oauth import parse_redirect

        parsed = urlparse(redirect_uri)
        host, port = parsed.hostname or "127.0.0.1", parsed.port or 80
        captured: dict[str, str] = {}

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                captured["path"] = self.path
                self.send_response(200)
                self.send_header("content-type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Authorization received. You can close this tab.")

            def log_message(self, *_: object) -> None:
                return None

        def _serve() -> None:
            server = HTTPServer((host, port), _Handler)
            server.timeout = self._timeout
            server.handle_request()
            server.server_close()

        (self._open_browser or webbrowser.open)(url)
        await asyncio.get_running_loop().run_in_executor(None, _serve)
        path = captured.get("path")
        if not path:
            raise OAuthError("the authorization redirect never arrived (timed out)")
        # ``path`` is the full request target ("/callback?code=...&state=..."); rebuild an
        # absolute URL from it so parse_redirect reads the query, not redirect_uri's.
        return parse_redirect(
            f"{parsed.scheme}://{parsed.netloc}{path}", expected_state=expected_state
        )


def default_oauth_driver(
    token_dir: str | Path, *, interactive: bool = False
) -> OAuthDriver:  # pragma: no cover - wires the impure adapters
    """A driver backed by the real edges: ``httpx``, the OS keyring, and system entropy.

    The store prefers the OS keyring (:class:`KeyringAuthStore`) so an attended ``ronin mcp
    login`` persists its token for later sessions; on a host with no keyring backend it
    degrades to :class:`FileAuthStore` under ``token_dir`` (the ``client_id`` stays durable,
    the token lives only for the process). ``interactive`` controls whether a browser
    authorizer is attached — session startup passes ``False`` so a missing token becomes a
    "log in first" failure rather than a browser that never opens; an attended login passes
    ``True``. Not exercised offline: the fetcher and authorizer it assembles open a socket
    and a browser.
    """
    import secrets
    import time

    keyring_store = KeyringAuthStore()
    store: AuthStore = keyring_store if keyring_store.durable else FileAuthStore(token_dir)
    return OAuthDriver(
        fetcher=HttpxFetcher(),
        store=store,
        now=time.time,
        new_verifier=lambda: secrets.token_urlsafe(64),
        new_state=lambda: secrets.token_urlsafe(32),
        authorizer=LoopbackAuthorizer() if interactive else None,
    )


__all__ = [
    "DEFAULT_REDIRECT_URI",
    "AuthStore",
    "Authorizer",
    "FileAuthStore",
    "HttpFetcher",
    "HttpReply",
    "HttpxFetcher",
    "InMemoryAuthStore",
    "KeyringAuthStore",
    "LoopbackAuthorizer",
    "OAuthDriver",
    "PersistedAuth",
    "ResolvedAuth",
    "authorize_servers",
    "default_oauth_driver",
    "token_refresher",
]
