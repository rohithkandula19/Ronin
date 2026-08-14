"""OAuth 2.1 protocol core — every transform exercised offline, `now` passed in.

The load-bearing assertion is the PKCE one: it uses the exact verifier/challenge pair from
RFC 7636 Appendix B, so a wrong S256 implementation fails here rather than against a real
authorization server. The rest pin the fail-closed rules — https-only endpoints, S256-only
PKCE, state checked on the redirect, resource carried through — because each is a security
property, not a nicety.
"""

from __future__ import annotations

import pytest

from ronin.mcp.oauth import (
    PKCE_METHOD,
    AuthServerMetadata,
    ClientRegistration,
    OAuthError,
    ProtectedResourceMetadata,
    TokenSet,
    authorization_code_request,
    authorization_url,
    parse_redirect,
    parse_www_authenticate,
    pkce_from_verifier,
    refresh_request,
    registration_request,
)

# RFC 7636 Appendix B test vector.
RFC7636_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC7636_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"

AUTHORIZE = "https://auth.example.com/authorize"
TOKEN = "https://auth.example.com/token"
RESOURCE = "https://mcp.example.com/"


def _meta(**over: object) -> AuthServerMetadata:
    data: dict[str, object] = {
        "issuer": "https://auth.example.com",
        "authorization_endpoint": AUTHORIZE,
        "token_endpoint": TOKEN,
        "code_challenge_methods_supported": ["S256"],
    }
    data.update(over)
    return AuthServerMetadata.from_json(data)


# --------------------------------------------------------------------------- #
# PKCE
# --------------------------------------------------------------------------- #


def test_pkce_matches_the_rfc7636_vector() -> None:
    pair = pkce_from_verifier(RFC7636_VERIFIER)
    assert pair.challenge == RFC7636_CHALLENGE
    assert pair.method == PKCE_METHOD


def test_pkce_rejects_a_too_short_verifier() -> None:
    with pytest.raises(OAuthError, match="43"):
        pkce_from_verifier("tooshort")


# --------------------------------------------------------------------------- #
# WWW-Authenticate
# --------------------------------------------------------------------------- #


def test_www_authenticate_extracts_bearer_params() -> None:
    header = (
        'Bearer resource_metadata="https://mcp.example.com/.well-known/x", error="invalid_token"'
    )
    params = parse_www_authenticate(header)
    assert params["resource_metadata"] == "https://mcp.example.com/.well-known/x"
    assert params["error"] == "invalid_token"


def test_www_authenticate_ignores_a_non_bearer_scheme() -> None:
    assert parse_www_authenticate('Basic realm="x"') == {}


# --------------------------------------------------------------------------- #
# metadata
# --------------------------------------------------------------------------- #


def test_protected_resource_metadata_requires_servers_and_resource() -> None:
    ok = ProtectedResourceMetadata.from_json(
        {"resource": RESOURCE, "authorization_servers": ["https://auth.example.com"]}
    )
    assert ok.authorization_servers == ("https://auth.example.com",)
    with pytest.raises(OAuthError, match="authorization_servers"):
        ProtectedResourceMetadata.from_json({"resource": RESOURCE})
    with pytest.raises(OAuthError, match="resource"):
        ProtectedResourceMetadata.from_json({"authorization_servers": ["https://a"]})


def test_auth_server_metadata_valid() -> None:
    meta = _meta(registration_endpoint="https://auth.example.com/register")
    assert meta.token_endpoint == TOKEN
    assert meta.registration_endpoint == "https://auth.example.com/register"


def test_auth_server_metadata_refuses_http_token_endpoint() -> None:
    with pytest.raises(OAuthError, match="token_endpoint must be https"):
        _meta(token_endpoint="http://auth.example.com/token")


def test_auth_server_metadata_allows_loopback_http() -> None:
    meta = _meta(
        authorization_endpoint="http://localhost:9000/authorize",
        token_endpoint="http://127.0.0.1:9000/token",
    )
    assert meta.token_endpoint.startswith("http://127.0.0.1")


def test_auth_server_metadata_refuses_non_s256_pkce() -> None:
    with pytest.raises(OAuthError, match="S256"):
        _meta(code_challenge_methods_supported=["plain"])


def test_auth_server_metadata_requires_token_endpoint() -> None:
    with pytest.raises(OAuthError, match="token_endpoint"):
        AuthServerMetadata.from_json({"issuer": "https://a", "authorization_endpoint": AUTHORIZE})


# --------------------------------------------------------------------------- #
# dynamic client registration
# --------------------------------------------------------------------------- #


def test_registration_request_is_a_public_pkce_client() -> None:
    body = registration_request(
        client_name="ronin", redirect_uris=["http://localhost:7777/cb"], scopes=["mcp:read"]
    )
    assert body["token_endpoint_auth_method"] == "none"
    assert body["grant_types"] == ["authorization_code", "refresh_token"]
    assert body["scope"] == "mcp:read"


def test_registration_request_needs_a_redirect_uri() -> None:
    with pytest.raises(OAuthError, match="redirect_uri"):
        registration_request(client_name="ronin", redirect_uris=[])


def test_client_registration_requires_client_id() -> None:
    assert ClientRegistration.from_json({"client_id": "abc"}).client_id == "abc"
    with pytest.raises(OAuthError, match="client_id"):
        ClientRegistration.from_json({"client_secret": "s"})


# --------------------------------------------------------------------------- #
# authorization request + redirect
# --------------------------------------------------------------------------- #


def test_authorization_url_carries_challenge_state_resource_scope() -> None:
    pkce = pkce_from_verifier(RFC7636_VERIFIER)
    url = authorization_url(
        _meta(),
        client_id="cid",
        redirect_uri="http://localhost:7777/cb",
        pkce=pkce,
        state="xyz",
        resource=RESOURCE,
        scopes=["mcp:read", "mcp:write"],
    )
    assert f"code_challenge={RFC7636_CHALLENGE}" in url
    assert "code_challenge_method=S256" in url
    assert "state=xyz" in url
    assert "response_type=code" in url
    assert "scope=mcp%3Aread+mcp%3Awrite" in url
    assert "verifier" not in url  # the secret half never travels


def test_authorization_url_requires_state() -> None:
    with pytest.raises(OAuthError, match="state"):
        authorization_url(
            _meta(),
            client_id="c",
            redirect_uri="http://localhost/cb",
            pkce=pkce_from_verifier(RFC7636_VERIFIER),
            state="",
            resource=RESOURCE,
        )


def test_parse_redirect_returns_code_when_state_matches() -> None:
    assert (
        parse_redirect("http://localhost/cb?code=abc123&state=xyz", expected_state="xyz")
        == "abc123"
    )


def test_parse_redirect_refuses_state_mismatch() -> None:
    with pytest.raises(OAuthError, match="state"):
        parse_redirect("http://localhost/cb?code=abc&state=evil", expected_state="xyz")


def test_parse_redirect_surfaces_server_error() -> None:
    with pytest.raises(OAuthError, match="access_denied"):
        parse_redirect("http://localhost/cb?error=access_denied&state=xyz", expected_state="xyz")


def test_parse_redirect_requires_a_code() -> None:
    with pytest.raises(OAuthError, match="no authorization code"):
        parse_redirect("http://localhost/cb?state=xyz", expected_state="xyz")


# --------------------------------------------------------------------------- #
# token requests
# --------------------------------------------------------------------------- #


def test_authorization_code_request_sends_verifier_and_resource() -> None:
    pkce = pkce_from_verifier(RFC7636_VERIFIER)
    req = authorization_code_request(
        _meta(),
        client_id="cid",
        code="thecode",
        pkce=pkce,
        redirect_uri="http://localhost:7777/cb",
        resource=RESOURCE,
    )
    assert req.url == TOKEN
    assert req.form["grant_type"] == "authorization_code"
    assert req.form["code_verifier"] == RFC7636_VERIFIER
    assert req.form["resource"] == RESOURCE
    assert "client_secret" not in req.form  # public client


def test_refresh_request_shape() -> None:
    req = refresh_request(_meta(), client_id="cid", refresh_token="rt", resource=RESOURCE)
    assert req.form["grant_type"] == "refresh_token"
    assert req.form["refresh_token"] == "rt"
    with pytest.raises(OAuthError, match="refresh_token"):
        refresh_request(_meta(), client_id="cid", refresh_token="", resource=RESOURCE)


# --------------------------------------------------------------------------- #
# token set
# --------------------------------------------------------------------------- #


def test_token_set_computes_absolute_expiry() -> None:
    ts = TokenSet.from_response(
        {
            "access_token": "at",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "rt",
            "scope": "mcp:read mcp:write",
        },
        now=1000.0,
        resource=RESOURCE,
    )
    assert ts.expires_at == 4600.0
    assert ts.scopes == ("mcp:read", "mcp:write")
    assert ts.authorization_header() == {"Authorization": "Bearer at"}
    assert not ts.is_expired(now=4000.0)
    assert ts.is_expired(now=4590.0)  # inside the 30s leeway


def test_token_set_refuses_non_bearer() -> None:
    with pytest.raises(OAuthError, match="token_type"):
        TokenSet.from_response(
            {"access_token": "at", "token_type": "mac"}, now=0.0, resource=RESOURCE
        )


def test_token_set_requires_access_token() -> None:
    with pytest.raises(OAuthError, match="access_token"):
        TokenSet.from_response({"token_type": "Bearer"}, now=0.0, resource=RESOURCE)


def test_token_set_unknown_expiry_is_not_expired_and_carries_refresh_forward() -> None:
    ts = TokenSet.from_response(
        {"access_token": "at2"}, now=0.0, resource=RESOURCE, fallback_refresh_token="old-rt"
    )
    assert ts.expires_at is None
    assert not ts.is_expired(now=10_000_000.0)  # nothing to act on proactively
    assert ts.refresh_token == "old-rt"  # refresh response omitted one; keep the old
