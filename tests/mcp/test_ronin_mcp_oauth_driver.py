"""The OAuth 2.1 live driver, exercised entirely offline.

:mod:`ronin.mcp.oauth` proved the transforms; this proves the *orchestration* that strings
them together, with all three impure edges — the HTTP client, the browser, the token store
— replaced by fakes. The load-bearing assertions are the fail-closed ones: a non-interactive
call with nothing cached refuses rather than opening a browser, a stored token that is still
fresh is reused with no network at all, and the authorization URL the "browser" is handed
carries the PKCE challenge and CSRF state (never the verifier). ``now`` / the verifier / the
state are all injected, so a whole flow is byte-for-byte reproducible.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from ronin.mcp.config import AuthKind, McpServerConfig, TransportKind
from ronin.mcp.oauth import OAuthError, TokenSet
from ronin.mcp.oauth_driver import (
    Authorizer,
    FileAuthStore,
    HttpFetcher,
    HttpReply,
    HttpxFetcher,
    InMemoryAuthStore,
    KeyringAuthStore,
    LoopbackAuthorizer,
    OAuthDriver,
    PersistedAuth,
    _well_known,
    authorize_servers,
)

RESOURCE = "https://mcp.example.com/mcp"
ISSUER = "https://auth.example.com"
TOKEN_ENDPOINT = "https://auth.example.com/token"
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"  # RFC 7636 Appendix B
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"

_PRM = {"resource": RESOURCE, "authorization_servers": [ISSUER], "scopes_supported": ["mcp:read"]}
_ASM = {
    "issuer": ISSUER,
    "authorization_endpoint": "https://auth.example.com/authorize",
    "token_endpoint": TOKEN_ENDPOINT,
    "registration_endpoint": "https://auth.example.com/register",
    "code_challenge_methods_supported": ["S256"],
}
_TOKEN = {
    "access_token": "at-1",
    "token_type": "Bearer",
    "expires_in": 3600,
    "refresh_token": "rt-1",
    "scope": "mcp:read",
}


class FakeFetcher(HttpFetcher):
    """A scripted :class:`~ronin.mcp.oauth_driver.HttpFetcher`. Records every call."""

    def __init__(
        self,
        *,
        token: Mapping[str, object] | None = None,
        registration: Mapping[str, object] | None = None,
        as_openid_only: bool = False,
        register_status: int = 201,
        token_status: int = 200,
    ) -> None:
        self._token = token if token is not None else _TOKEN
        self._registration = registration if registration is not None else {"client_id": "reg-1"}
        self._as_openid_only = as_openid_only
        self._register_status = register_status
        self._token_status = token_status
        self.gets: list[str] = []
        self.form_posts: list[tuple[str, Mapping[str, str]]] = []
        self.json_posts: list[tuple[str, Mapping[str, object]]] = []

    async def get(self, url: str) -> HttpReply:
        self.gets.append(url)
        if "oauth-protected-resource" in url:
            return HttpReply(200, json.dumps(_PRM).encode())
        if "oauth-authorization-server" in url:
            if self._as_openid_only:
                return HttpReply(404, b"not here")
            return HttpReply(200, json.dumps(_ASM).encode())
        if "openid-configuration" in url:
            return HttpReply(200, json.dumps(_ASM).encode())
        return HttpReply(404, b"")

    async def post_json(self, url: str, body: Mapping[str, object]) -> HttpReply:
        self.json_posts.append((url, dict(body)))
        return HttpReply(self._register_status, json.dumps(self._registration).encode())

    async def post_form(self, url: str, form: Mapping[str, str]) -> HttpReply:
        self.form_posts.append((url, dict(form)))
        return HttpReply(self._token_status, json.dumps(self._token).encode())


class FakeAuthorizer(Authorizer):
    """Stands in for the browser + loopback capture: returns a fixed code, records the URL."""

    def __init__(self, code: str = "the-code") -> None:
        self._code = code
        self.seen_url = ""

    async def authorize(self, url: str, *, redirect_uri: str, expected_state: str) -> str:
        self.seen_url = url
        return self._code


def _driver(
    *,
    fetcher: FakeFetcher | None = None,
    store: InMemoryAuthStore | None = None,
    authorizer: FakeAuthorizer | None = None,
    now: float = 1000.0,
) -> tuple[OAuthDriver, FakeFetcher, InMemoryAuthStore]:
    fetcher = fetcher or FakeFetcher()
    store = store or InMemoryAuthStore()
    driver = OAuthDriver(
        fetcher=fetcher,
        store=store,
        now=lambda: now,
        new_verifier=lambda: VERIFIER,
        new_state=lambda: "csrf-state",
        authorizer=authorizer,
    )
    return driver, fetcher, store


def _config(name: str = "remote", *, url: str = RESOURCE) -> McpServerConfig:
    return McpServerConfig(name=name, transport=TransportKind.HTTP, url=url, auth=AuthKind.OAUTH)


# --------------------------------------------------------------------------- #
# ensure: reuse / refresh / refuse
# --------------------------------------------------------------------------- #


async def test_ensure_reuses_a_fresh_cached_token_without_any_network() -> None:
    store = InMemoryAuthStore()
    cached = TokenSet(access_token="cached", resource=RESOURCE, expires_at=9999.0)
    store.save("remote", PersistedAuth(client_id="c", token=cached))
    driver, fetcher, _ = _driver(store=store, now=1000.0)

    token = await driver.ensure(_config())

    assert token.access_token == "cached"
    assert fetcher.gets == [] and fetcher.form_posts == []  # nothing was fetched


async def test_ensure_refuses_when_nothing_cached_and_non_interactive() -> None:
    driver, _, _ = _driver()
    with pytest.raises(OAuthError, match="interactive login"):
        await driver.ensure(_config(), interactive=False)


async def test_ensure_refreshes_an_expiring_token_and_persists_it() -> None:
    store = InMemoryAuthStore()
    stale = TokenSet(
        access_token="old", resource=RESOURCE, refresh_token="rt-old", expires_at=1000.0
    )
    store.save("remote", PersistedAuth(client_id="client-9", token=stale))
    driver, fetcher, _ = _driver(store=store, now=2000.0)  # past expiry

    token = await driver.ensure(_config())

    assert token.access_token == "at-1"
    url, form = fetcher.form_posts[-1]
    assert url == TOKEN_ENDPOINT
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == "rt-old"
    assert form["client_id"] == "client-9"
    # the fresh token is what a subsequent ensure() would reuse
    saved = store.load("remote")
    assert saved is not None and saved.token is not None
    assert saved.token.access_token == "at-1"


async def test_refresh_carries_the_old_refresh_token_forward_when_omitted() -> None:
    store = InMemoryAuthStore()
    stale = TokenSet(access_token="old", resource=RESOURCE, refresh_token="rt-keep", expires_at=0.0)
    store.save("remote", PersistedAuth(client_id="c", token=stale))
    fetcher = FakeFetcher(token={"access_token": "at-2", "token_type": "Bearer"})  # no refresh
    driver, _, _ = _driver(store=store, fetcher=fetcher, now=10.0)

    token = await driver.ensure(_config())

    assert token.access_token == "at-2"
    assert token.refresh_token == "rt-keep"  # not lost


async def test_refresh_without_a_refresh_token_refuses() -> None:
    driver, _, _ = _driver()
    no_refresh = TokenSet(access_token="x", resource=RESOURCE)
    with pytest.raises(OAuthError, match="no refresh_token"):
        await driver.refresh(_config(), no_refresh)


# --------------------------------------------------------------------------- #
# obtain: the full authorization-code + PKCE flow
# --------------------------------------------------------------------------- #


async def test_obtain_runs_discovery_registration_authorization_and_exchange() -> None:
    authz = FakeAuthorizer()
    driver, fetcher, store = _driver(authorizer=authz)

    token = await driver.obtain(_config())

    assert token.access_token == "at-1"
    # dynamic registration happened (no client_id was on record)
    assert fetcher.json_posts and fetcher.json_posts[0][0] == "https://auth.example.com/register"
    # the browser was handed a URL carrying the challenge and state, never the verifier
    assert f"code_challenge={CHALLENGE}" in authz.seen_url
    assert "state=csrf-state" in authz.seen_url
    assert VERIFIER not in authz.seen_url
    # the exchange proved possession of the verifier and pinned the resource
    _, form = fetcher.form_posts[-1]
    assert form["grant_type"] == "authorization_code"
    assert form["code_verifier"] == VERIFIER
    assert form["resource"] == RESOURCE
    # the client id from registration and the token were persisted together
    saved = store.load("remote")
    assert saved is not None and saved.token is not None
    assert saved.client_id == "reg-1" and saved.token.access_token == "at-1"


async def test_obtain_reuses_a_registered_client_and_skips_registration() -> None:
    store = InMemoryAuthStore()
    store.save("remote", PersistedAuth(client_id="already-registered"))
    driver, fetcher, _ = _driver(store=store, authorizer=FakeAuthorizer())

    await driver.obtain(_config())

    assert fetcher.json_posts == []  # no re-registration
    _, form = fetcher.form_posts[-1]
    assert form["client_id"] == "already-registered"


async def test_obtain_without_an_authorizer_refuses() -> None:
    driver, _, _ = _driver(authorizer=None)
    with pytest.raises(OAuthError, match="interactive authorizer"):
        await driver.obtain(_config())


async def test_ensure_interactive_obtains_when_nothing_is_cached() -> None:
    driver, _, store = _driver(authorizer=FakeAuthorizer())
    token = await driver.ensure(_config(), interactive=True)
    assert token.access_token == "at-1"
    saved = store.load("remote")
    assert saved is not None and saved.token is not None


async def test_registration_failure_surfaces_its_status() -> None:
    fetcher = FakeFetcher(register_status=400, registration={"error": "nope"})
    driver, _, _ = _driver(fetcher=fetcher, authorizer=FakeAuthorizer())
    with pytest.raises(OAuthError, match="registration failed"):
        await driver.obtain(_config())


async def test_token_endpoint_error_surfaces_its_status() -> None:
    fetcher = FakeFetcher(token_status=401, token={"error": "invalid_grant"})
    driver, _, _ = _driver(fetcher=fetcher, authorizer=FakeAuthorizer())
    with pytest.raises(OAuthError, match="token endpoint returned 401"):
        await driver.obtain(_config())


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #


async def test_discover_falls_back_to_openid_configuration() -> None:
    driver, fetcher, _ = _driver(fetcher=FakeFetcher(as_openid_only=True))
    _, asm = await driver.discover(RESOURCE)
    assert asm.token_endpoint == TOKEN_ENDPOINT
    assert any("openid-configuration" in url for url in fetcher.gets)


async def test_discover_refuses_when_no_metadata_is_published() -> None:
    class Empty(FakeFetcher):
        async def get(self, url: str) -> HttpReply:
            self.gets.append(url)
            if "oauth-protected-resource" in url:
                return HttpReply(200, json.dumps(_PRM).encode())
            return HttpReply(404, b"")

    driver, _, _ = _driver(fetcher=Empty())
    with pytest.raises(OAuthError, match="no usable metadata"):
        await driver.discover(RESOURCE)


async def test_refresh_without_a_client_id_on_record_refuses() -> None:
    store = InMemoryAuthStore()
    token = TokenSet(access_token="x", resource=RESOURCE, refresh_token="rt", expires_at=0.0)
    store.save("remote", PersistedAuth(client_id="", token=token))  # never registered
    driver, _, _ = _driver(store=store, now=10.0)
    with pytest.raises(OAuthError, match="no registered client_id"):
        await driver.ensure(_config())


async def test_obtain_refuses_when_no_client_id_and_no_registration_endpoint() -> None:
    class NoRegistration(FakeFetcher):
        async def get(self, url: str) -> HttpReply:
            self.gets.append(url)
            if "oauth-protected-resource" in url:
                return HttpReply(200, json.dumps(_PRM).encode())
            no_reg = {k: v for k, v in _ASM.items() if k != "registration_endpoint"}
            return HttpReply(200, json.dumps(no_reg).encode())

    driver, _, _ = _driver(fetcher=NoRegistration(), authorizer=FakeAuthorizer())
    with pytest.raises(OAuthError, match="no dynamic registration"):
        await driver.obtain(_config())


async def test_discover_surfaces_a_non_200_metadata_response() -> None:
    class Missing(FakeFetcher):
        async def get(self, url: str) -> HttpReply:
            self.gets.append(url)
            return HttpReply(404, b"gone")

    driver, _, _ = _driver(fetcher=Missing())
    with pytest.raises(OAuthError, match="returned 404"):
        await driver.discover(RESOURCE)


def test_well_known_inserts_the_suffix_before_a_tenant_path() -> None:
    assert (
        _well_known("https://host/tenant", "oauth-authorization-server")
        == "https://host/.well-known/oauth-authorization-server/tenant"
    )
    assert (
        _well_known("https://host", "oauth-protected-resource")
        == "https://host/.well-known/oauth-protected-resource"
    )


# --------------------------------------------------------------------------- #
# guards + persistence
# --------------------------------------------------------------------------- #


async def test_ensure_rejects_a_non_oauth_config() -> None:
    driver, _, _ = _driver()
    plain = McpServerConfig(name="x", transport=TransportKind.HTTP, url=RESOURCE)
    with pytest.raises(OAuthError, match="not configured for OAuth"):
        await driver.ensure(plain)


def test_http_reply_json_rejects_a_non_object() -> None:
    with pytest.raises(OAuthError, match="not a JSON object"):
        HttpReply(200, b"[1, 2, 3]").json(what="thing")
    with pytest.raises(OAuthError, match="not JSON"):
        HttpReply(200, b"<html>").json(what="thing")


def test_in_memory_store_deletes() -> None:
    store = InMemoryAuthStore()
    store.save("k", PersistedAuth(client_id="c"))
    store.delete("k")
    assert store.load("k") is None
    store.delete("k")  # deleting a missing key is a no-op, not an error


def test_file_store_persists_the_client_id_but_never_the_token(tmp_path: Path) -> None:
    """The load-bearing security assertion: a bearer token must not reach the disk."""
    token = TokenSet(access_token="acc-plain", resource=RESOURCE, refresh_token="ref-plain")
    FileAuthStore(tmp_path).save("remote", PersistedAuth(client_id="cid-1", token=token))

    on_disk = (tmp_path / "remote.json").read_text(encoding="utf-8")
    assert "cid-1" in on_disk  # the non-secret registration is durable
    assert "acc-plain" not in on_disk and "ref-plain" not in on_disk  # the token is not

    # A fresh store (a new process) recovers the client_id but not the session-only token.
    reloaded = FileAuthStore(tmp_path).load("remote")
    assert reloaded is not None
    assert reloaded.client_id == "cid-1" and reloaded.token is None


def test_file_store_reuses_the_token_within_one_session(tmp_path: Path) -> None:
    store = FileAuthStore(tmp_path)
    token = TokenSet(access_token="at", resource=RESOURCE)
    store.save("remote", PersistedAuth(client_id="cid", token=token))
    loaded = store.load("remote")
    assert loaded is not None and loaded.token is not None
    assert loaded.token.access_token == "at"  # same instance keeps it in memory


def test_file_store_tolerates_a_corrupt_client_id_file(tmp_path: Path) -> None:
    (tmp_path / "remote.json").write_text("{ not json", encoding="utf-8")
    assert FileAuthStore(tmp_path).load("remote") is None  # unreadable → treated as absent


def test_file_store_load_is_none_for_an_unknown_server_and_delete_clears(tmp_path: Path) -> None:
    store = FileAuthStore(tmp_path)
    assert store.load("never-seen") is None
    store.save("remote", PersistedAuth(client_id="cid", token=TokenSet("at", RESOURCE)))
    store.delete("remote")
    assert store.load("remote") is None
    store.delete("remote")  # deleting a missing key is a no-op


def test_the_network_adapters_construct_without_touching_their_edges() -> None:
    """Constructing the socket/browser adapters opens nothing — the I/O is deferred."""
    assert isinstance(HttpxFetcher(timeout=1.0), HttpxFetcher)
    assert isinstance(LoopbackAuthorizer(timeout=1.0), LoopbackAuthorizer)


# --------------------------------------------------------------------------- #
# KeyringAuthStore — token durable in an encrypted store, memory fallback
# --------------------------------------------------------------------------- #


class _FakeKeyring:
    """A stand-in for the ``keyring`` module: a dict keyed by (service, name)."""

    class errors:
        class KeyringError(Exception):
            pass

    def __init__(self, *, fail: bool = False) -> None:
        self._store: dict[tuple[str, str], str] = {}
        self._fail = fail

    def seed(self, service: str, name: str, value: str) -> None:
        """Put a raw value in the store directly, bypassing the keyring API."""
        self._store[(service, name)] = value

    def get_password(self, service: str, name: str) -> str | None:
        if self._fail:
            raise self.errors.KeyringError("no backend")
        return self._store.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        if self._fail:
            raise self.errors.KeyringError("no backend")
        self._store[(service, name)] = value

    def delete_password(self, service: str, name: str) -> None:
        if (service, name) not in self._store:
            raise self.errors.KeyringError("absent")
        del self._store[(service, name)]


def test_keyring_store_round_trips_the_token_through_the_encrypted_store() -> None:
    store = KeyringAuthStore(backend=_FakeKeyring())
    assert store.durable
    token = TokenSet("at", RESOURCE, refresh_token="rt", expires_at=4600.0, scopes=("mcp:read",))
    store.save("remote", PersistedAuth(client_id="cid", token=token))

    loaded = store.load("remote")
    assert loaded is not None and loaded.token is not None
    # Unlike the file store, the token itself survives here — that is the point of the keyring.
    assert loaded.client_id == "cid"
    assert loaded.token.access_token == "at" and loaded.token.refresh_token == "rt"
    assert loaded.token.scopes == ("mcp:read",)


def test_keyring_store_without_a_backend_is_memory_only() -> None:
    store = KeyringAuthStore(backend=None)
    assert not store.durable
    store.save("remote", PersistedAuth(client_id="cid", token=TokenSet("at", RESOURCE)))
    loaded = store.load("remote")
    assert loaded is not None and loaded.token is not None and loaded.token.access_token == "at"
    assert store.load("never") is None


def test_keyring_store_falls_back_to_memory_when_the_backend_errors() -> None:
    store = KeyringAuthStore(backend=_FakeKeyring(fail=True))
    assert store.durable  # a backend is present...
    store.save("remote", PersistedAuth(client_id="cid", token=TokenSet("at", RESOURCE)))
    # ...but every call raises, so save/load quietly use the in-memory overlay instead.
    loaded = store.load("remote")
    assert loaded is not None and loaded.token is not None and loaded.token.access_token == "at"


def test_keyring_store_load_is_none_for_unknown_and_corrupt_values() -> None:
    backend = _FakeKeyring()
    store = KeyringAuthStore(backend=backend)
    assert store.load("never") is None
    backend.seed("ronin-mcp-oauth", "corrupt", "{ not json")  # a value that will not parse
    assert store.load("corrupt") is None


def test_keyring_store_delete_removes_the_entry() -> None:
    store = KeyringAuthStore(backend=_FakeKeyring())
    store.save("remote", PersistedAuth(client_id="cid", token=TokenSet("at", RESOURCE)))
    store.delete("remote")
    assert store.load("remote") is None
    store.delete("remote")  # deleting a missing entry is a no-op, not an error


# --------------------------------------------------------------------------- #
# authorize_servers: the wiring seam
# --------------------------------------------------------------------------- #


async def test_authorize_servers_splits_wins_from_losses_and_ignores_non_oauth() -> None:
    store = InMemoryAuthStore()
    fresh = TokenSet("tok", RESOURCE, expires_at=9999.0)
    store.save("ok", PersistedAuth(client_id="c", token=fresh))
    driver, _, _ = _driver(store=store, now=1000.0)
    configs = (
        _config("ok"),
        _config("needs-login"),
        McpServerConfig(name="plain", transport=TransportKind.HTTP, url=RESOURCE),
    )

    resolved = await authorize_servers(configs, driver, interactive=False)

    assert resolved.headers == {"ok": {"Authorization": "Bearer tok"}}
    assert "needs-login" in resolved.failures
    assert "plain" not in resolved.headers and "plain" not in resolved.failures
