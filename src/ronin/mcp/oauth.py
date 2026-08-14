"""OAuth 2.1 for remote MCP servers — the protocol core, as pure data transforms.

A remote MCP server may sit behind OAuth 2.1 (the MCP authorization spec, built on
RFC 6749/8414/9728/7591/7636). Today Ronin can only send a *static* bearer header from
config; this module is the machinery for the real flow — discover the authorization
server from a ``401``, register a client dynamically, run authorization-code + PKCE, and
exchange/refresh tokens.

**Everything here is pure.** No socket is opened, no browser is launched, no clock is
read from inside. The impure parts — the HTTPS calls, the browser redirect, the random
bytes, the wall clock, and where tokens are stored — are the caller's to inject, exactly
like :mod:`ronin.cli.detect` injects its probes. That is what lets a security-critical
flow be tested exhaustively offline: a token exchange is ``(endpoint, form)`` in and a
:class:`TokenSet` out, with `now` passed as an argument.

Fail-closed choices worth stating:

* **PKCE is mandatory and S256-only.** ``plain`` is refused even if a server offers it —
  a downgraded challenge is the vulnerability PKCE exists to close.
* **Every endpoint URL must be HTTPS** (loopback ``http`` is allowed for a local dev
  server, nothing else): an ``http`` token endpoint would send the code and the client
  secret in the clear.
* **A missing required field is an error, not a default.** No token-endpoint URL means no
  exchange is attempted — there is no fallback that could leak a code somewhere unintended.
* **The resource is pinned.** The token is requested and later sent for one ``resource``
  (RFC 8707), so a token minted for server A is never replayed to server B.

The live driver that wires these transforms to a real HTTP client, an actual browser, and
a token store is deliberately a separate change: the part that must be exactly right is
the protocol, and it is all here and all tested.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse

from .jsonrpc import McpError

#: The only PKCE method accepted. ``plain`` is deliberately unsupported.
PKCE_METHOD = "S256"

#: RFC 7636 §4.1 — the verifier is 43-128 chars from the unreserved set.
_MIN_VERIFIER, _MAX_VERIFIER = 43, 128


class OAuthError(McpError):
    """An OAuth flow could not proceed safely. Raised rather than degraded."""


def _require_secure(url: str, *, what: str) -> str:
    """Return ``url`` if it is https (or loopback http), else refuse.

    A cleartext authorization or token endpoint would put the code, the client secret, and
    the access token on the wire in the open, so it is refused rather than used.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    is_loopback = host in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme == "https" or (parsed.scheme == "http" and is_loopback):
        return url
    raise OAuthError(f"{what} must be https (loopback http is the only exception): {url!r}")


# --------------------------------------------------------------------------- #
# PKCE (RFC 7636)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PkcePair:
    """A PKCE verifier and its S256 challenge."""

    verifier: str
    challenge: str
    method: str = PKCE_METHOD


def pkce_from_verifier(verifier: str) -> PkcePair:
    """Derive the S256 challenge for a caller-supplied verifier.

    The verifier is the caller's random secret (generate it with ``secrets.token_urlsafe``
    — this module never touches randomness so it stays pure and testable). Its length is
    checked against RFC 7636 so a too-short, low-entropy verifier is refused here rather
    than by the authorization server after the round trip.
    """
    if not _MIN_VERIFIER <= len(verifier) <= _MAX_VERIFIER:
        raise OAuthError(
            f"PKCE verifier must be {_MIN_VERIFIER}-{_MAX_VERIFIER} chars, got {len(verifier)}"
        )
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return PkcePair(verifier=verifier, challenge=challenge)


# --------------------------------------------------------------------------- #
# WWW-Authenticate (RFC 9728 §5.1) — where discovery starts
# --------------------------------------------------------------------------- #


def parse_www_authenticate(header: str) -> Mapping[str, str]:
    """The ``Bearer`` params from a ``401``'s ``WWW-Authenticate`` header.

    A protected MCP server answers an unauthenticated request with
    ``WWW-Authenticate: Bearer resource_metadata="https://…", error="…"``. Only the
    ``Bearer`` scheme is understood; its comma-separated ``key="value"`` params are
    returned lower-cased. A header that names another scheme yields an empty mapping — the
    caller then knows this is not an OAuth challenge it can act on.
    """
    header = header.strip()
    scheme, _, rest = header.partition(" ")
    if scheme.lower() != "bearer":
        return {}
    params: dict[str, str] = {}
    for part in rest.split(","):
        key, sep, value = part.strip().partition("=")
        if sep:
            params[key.strip().lower()] = value.strip().strip('"')
    return params


# --------------------------------------------------------------------------- #
# Metadata: protected resource (RFC 9728) and authorization server (RFC 8414)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ProtectedResourceMetadata:
    """What a protected MCP server publishes about itself (RFC 9728)."""

    resource: str
    authorization_servers: tuple[str, ...]
    scopes_supported: tuple[str, ...] = ()

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> ProtectedResourceMetadata:
        servers = _str_tuple(data.get("authorization_servers"))
        if not servers:
            raise OAuthError("protected-resource metadata lists no authorization_servers")
        resource = data.get("resource")
        if not isinstance(resource, str) or not resource:
            raise OAuthError("protected-resource metadata has no 'resource'")
        return cls(
            resource=resource,
            authorization_servers=servers,
            scopes_supported=_str_tuple(data.get("scopes_supported")),
        )


@dataclass(frozen=True, slots=True)
class AuthServerMetadata:
    """An authorization server's advertised endpoints and capabilities (RFC 8414)."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str = ""
    scopes_supported: tuple[str, ...] = ()
    code_challenge_methods_supported: tuple[str, ...] = ()
    grant_types_supported: tuple[str, ...] = ()

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> AuthServerMetadata:
        issuer = _required(data, "issuer")
        authorize = _require_secure(
            _required(data, "authorization_endpoint"), what="authorization_endpoint"
        )
        token = _require_secure(_required(data, "token_endpoint"), what="token_endpoint")
        methods = _str_tuple(data.get("code_challenge_methods_supported"))
        # An empty list is tolerated (some servers omit it) but a present list that lacks
        # S256 is a hard stop: it means the server cannot do the only method we will use.
        if methods and PKCE_METHOD not in methods:
            raise OAuthError(
                f"authorization server does not support {PKCE_METHOD} PKCE "
                f"(offers {list(methods)}); refusing to downgrade"
            )
        registration = data.get("registration_endpoint")
        registration = (
            _require_secure(registration, what="registration_endpoint")
            if isinstance(registration, str) and registration
            else ""
        )
        return cls(
            issuer=issuer,
            authorization_endpoint=authorize,
            token_endpoint=token,
            registration_endpoint=registration,
            scopes_supported=_str_tuple(data.get("scopes_supported")),
            code_challenge_methods_supported=methods,
            grant_types_supported=_str_tuple(data.get("grant_types_supported")),
        )


# --------------------------------------------------------------------------- #
# Dynamic client registration (RFC 7591)
# --------------------------------------------------------------------------- #


def registration_request(
    *, client_name: str, redirect_uris: Sequence[str], scopes: Sequence[str] = ()
) -> Mapping[str, object]:
    """The POST body to register a public client doing authorization-code + PKCE."""
    if not redirect_uris:
        raise OAuthError("dynamic client registration needs at least one redirect_uri")
    body: dict[str, object] = {
        "client_name": client_name,
        "redirect_uris": list(redirect_uris),
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",  # a public client keeps no secret
    }
    if scopes:
        body["scope"] = " ".join(scopes)
    return body


@dataclass(frozen=True, slots=True)
class ClientRegistration:
    """The client identity an authorization server issued (RFC 7591 response)."""

    client_id: str
    client_secret: str = ""

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> ClientRegistration:
        client_id = data.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise OAuthError("client registration response has no client_id")
        secret = data.get("client_secret")
        return cls(client_id=client_id, client_secret=secret if isinstance(secret, str) else "")


# --------------------------------------------------------------------------- #
# Authorization request (RFC 6749 §4.1 + PKCE + RFC 8707 resource)
# --------------------------------------------------------------------------- #


def authorization_url(
    metadata: AuthServerMetadata,
    *,
    client_id: str,
    redirect_uri: str,
    pkce: PkcePair,
    state: str,
    resource: str,
    scopes: Sequence[str] = (),
) -> str:
    """The URL to send the user to, to approve access.

    Carries the PKCE *challenge* (never the verifier), the ``state`` the caller will check
    on the redirect back (CSRF defence), and the ``resource`` the token is being requested
    for (RFC 8707), so the issued token is bound to this one server.
    """
    if not state:
        raise OAuthError("authorization_url needs a non-empty state (CSRF protection)")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": pkce.challenge,
        "code_challenge_method": pkce.method,
        "state": state,
        "resource": resource,
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    sep = "&" if urlparse(metadata.authorization_endpoint).query else "?"
    return f"{metadata.authorization_endpoint}{sep}{urlencode(params)}"


def parse_redirect(redirect_url: str, *, expected_state: str) -> str:
    """The authorization code from the redirect, after checking ``state``.

    Refuses a mismatched or missing ``state`` (a forged/replayed redirect) and surfaces an
    ``error=`` the server sent instead of a code, rather than proceeding to exchange nothing.
    """
    query = urlparse(redirect_url).query
    parsed: dict[str, str] = {}
    for part in query.split("&"):
        key, sep, value = part.partition("=")
        if sep:
            from urllib.parse import unquote_plus

            parsed[key] = unquote_plus(value)
    if "error" in parsed:
        raise OAuthError(f"authorization server returned error: {parsed['error']}")
    if parsed.get("state") != expected_state:
        raise OAuthError("redirect 'state' does not match; refusing (possible CSRF/replay)")
    code = parsed.get("code")
    if not code:
        raise OAuthError("redirect carried no authorization code")
    return code


# --------------------------------------------------------------------------- #
# Token requests (RFC 6749 §4.1.3 exchange, §6 refresh)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TokenRequest:
    """A ready-to-POST token request: where to send it, and the form body."""

    url: str
    form: Mapping[str, str]


def authorization_code_request(
    metadata: AuthServerMetadata,
    *,
    client_id: str,
    code: str,
    pkce: PkcePair,
    redirect_uri: str,
    resource: str,
    client_secret: str = "",
) -> TokenRequest:
    """Exchange an authorization code for tokens, proving possession of the PKCE verifier."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": pkce.verifier,
        "resource": resource,
    }
    if client_secret:
        form["client_secret"] = client_secret
    return TokenRequest(url=metadata.token_endpoint, form=form)


def refresh_request(
    metadata: AuthServerMetadata,
    *,
    client_id: str,
    refresh_token: str,
    resource: str,
    client_secret: str = "",
    scopes: Sequence[str] = (),
) -> TokenRequest:
    """Trade a refresh token for a fresh access token, for the same resource."""
    if not refresh_token:
        raise OAuthError("refresh_request needs a refresh_token")
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "resource": resource,
    }
    if client_secret:
        form["client_secret"] = client_secret
    if scopes:
        form["scope"] = " ".join(scopes)
    return TokenRequest(url=metadata.token_endpoint, form=form)


# --------------------------------------------------------------------------- #
# Token set
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TokenSet:
    """Tokens held for one resource, with an absolute expiry so ``now`` decides freshness."""

    access_token: str
    resource: str
    refresh_token: str = ""
    #: Absolute unix expiry (seconds). ``None`` means the server gave no ``expires_in`` —
    #: treated as "expiry unknown", so a proactive refresh is skipped but a 401 still
    #: triggers one. Never guessed at a default lifetime.
    expires_at: float | None = None
    scopes: tuple[str, ...] = ()

    @classmethod
    def from_response(
        cls,
        data: Mapping[str, object],
        *,
        now: float,
        resource: str,
        fallback_refresh_token: str = "",
    ) -> TokenSet:
        """Build from a token-endpoint JSON response.

        ``fallback_refresh_token`` carries the previous refresh token forward when a refresh
        response omits a new one (permitted by RFC 6749 §6). ``token_type`` must be Bearer —
        anything else is a scheme this client cannot send and is refused.
        """
        access = data.get("access_token")
        if not isinstance(access, str) or not access:
            raise OAuthError("token response has no access_token")
        token_type = str(data.get("token_type", "")).lower()
        if token_type and token_type != "bearer":
            raise OAuthError(f"unsupported token_type {token_type!r}; only Bearer is handled")
        expires_at: float | None = None
        expires_in = data.get("expires_in")
        if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
            expires_at = now + float(expires_in)
        refresh = data.get("refresh_token")
        refresh_token = refresh if isinstance(refresh, str) and refresh else fallback_refresh_token
        scope = data.get("scope")
        scopes = tuple(scope.split()) if isinstance(scope, str) and scope else ()
        return cls(
            access_token=access,
            resource=resource,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scopes=scopes,
        )

    def is_expired(self, now: float, *, leeway: float = 30.0) -> bool:
        """True when the access token is at/near expiry (a ``leeway`` guards the round trip).

        Unknown expiry (``expires_at is None``) is reported as *not* expired: there is
        nothing to act on proactively, and a real 401 is what forces a refresh then.
        """
        return self.expires_at is not None and now >= self.expires_at - leeway

    def authorization_header(self) -> dict[str, str]:
        """The header to attach to an MCP request bound to this token's resource."""
        return {"Authorization": f"Bearer {self.access_token}"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _required(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise OAuthError(f"metadata is missing required string field {key!r}")
    return value


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


__all__ = [
    "PKCE_METHOD",
    "AuthServerMetadata",
    "ClientRegistration",
    "OAuthError",
    "PkcePair",
    "ProtectedResourceMetadata",
    "TokenRequest",
    "TokenSet",
    "authorization_code_request",
    "authorization_url",
    "parse_redirect",
    "parse_www_authenticate",
    "pkce_from_verifier",
    "refresh_request",
    "registration_request",
]
