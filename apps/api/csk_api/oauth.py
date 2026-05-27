"""OAuth flows for Stripe / Linear / Slack.

Each provider has the same shape:
- ``start_<provider>_oauth(user)`` → builds the redirect URL + persists a state token
- ``<provider>_oauth_callback(state, code)`` → exchanges the code for an access token
  and stores it as a ServiceConnection

You register OAuth apps in each provider's developer console first:
- Stripe Connect:  https://dashboard.stripe.com/settings/connect/onboarding-options
- Linear OAuth:    https://linear.app/settings/api/applications/new
- Slack apps:      https://api.slack.com/apps

Set client ID / secret as env vars per the names below, then wire the callback
URLs (also below) into each provider's app config.
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.orm import Session

from .db import ServiceConnection
from .services import store_connection


# ---------- env-driven provider config ----------

@dataclass(frozen=True)
class OAuthProviderConfig:
    name: str               # short id we store under in service_connections
    auth_url: str           # provider's authorize endpoint
    token_url: str          # provider's token-exchange endpoint
    scopes: str             # space- or comma-separated, provider-specific
    client_id_env: str
    client_secret_env: str
    redirect_uri_env: str   # env var holding the callback URL we registered

    @property
    def client_id(self) -> str:
        return os.environ.get(self.client_id_env, "")

    @property
    def client_secret(self) -> str:
        return os.environ.get(self.client_secret_env, "")

    @property
    def redirect_uri(self) -> str:
        return os.environ.get(self.redirect_uri_env, "")

    def assert_configured(self) -> None:
        missing = [
            n for n, v in [
                (self.client_id_env, self.client_id),
                (self.client_secret_env, self.client_secret),
                (self.redirect_uri_env, self.redirect_uri),
            ] if not v
        ]
        if missing:
            raise RuntimeError(f"OAuth provider {self.name!r} not configured — missing env vars: {missing}")


PROVIDERS: dict[str, OAuthProviderConfig] = {
    "stripe": OAuthProviderConfig(
        name="stripe",
        # Stripe Connect uses standard OAuth 2 against connect.stripe.com
        auth_url="https://connect.stripe.com/oauth/authorize",
        token_url="https://connect.stripe.com/oauth/token",
        scopes="read_only",
        client_id_env="STRIPE_CLIENT_ID",
        client_secret_env="STRIPE_SECRET_KEY",  # Stripe uses the secret key as the auth on /oauth/token
        redirect_uri_env="STRIPE_REDIRECT_URI",
    ),
    "linear": OAuthProviderConfig(
        name="linear",
        auth_url="https://linear.app/oauth/authorize",
        token_url="https://api.linear.app/oauth/token",
        scopes="read",
        client_id_env="LINEAR_CLIENT_ID",
        client_secret_env="LINEAR_CLIENT_SECRET",
        redirect_uri_env="LINEAR_REDIRECT_URI",
    ),
    "slack_bot": OAuthProviderConfig(
        name="slack_bot",
        auth_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        scopes="chat:write,channels:read,channels:history,users:read",
        client_id_env="SLACK_CLIENT_ID",
        client_secret_env="SLACK_CLIENT_SECRET",
        redirect_uri_env="SLACK_REDIRECT_URI",
    ),
}


# ---------- state-token store (in-memory MVP; swap to Redis in prod) ----------

# Maps state_token -> (user_id, provider, created_at)
_STATE_STORE: dict[str, tuple[int, str, float]] = {}
_STATE_TTL_SECONDS = 600  # 10 minutes


def _new_state(user_id: int, provider: str) -> str:
    token = secrets.token_urlsafe(32)
    _STATE_STORE[token] = (user_id, provider, time.time())
    return token


def _consume_state(state: str, expected_provider: str) -> int:
    """Validate the state token and return the user_id. Raises on bad/expired/wrong-provider tokens."""
    record = _STATE_STORE.pop(state, None)
    if record is None:
        raise ValueError("invalid or already-used oauth state")
    user_id, provider, created_at = record
    if provider != expected_provider:
        raise ValueError(f"state token was issued for {provider!r}, not {expected_provider!r}")
    if time.time() - created_at > _STATE_TTL_SECONDS:
        raise ValueError("oauth state expired — restart the flow")
    return user_id


# ---------- public API ----------

def build_authorize_url(provider_key: str, user_id: int) -> str:
    """Build the URL we redirect the user to so they can approve our app."""
    cfg = PROVIDERS.get(provider_key)
    if cfg is None:
        raise ValueError(f"unknown provider {provider_key!r}")
    cfg.assert_configured()

    state = _new_state(user_id, provider_key)
    params = {
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "response_type": "code",
        "scope": cfg.scopes,
        "state": state,
    }
    # Stripe uses 'response_type=code' implicit; Slack uses scope; Linear uses scope.
    # We pass scope on every provider; harmless when ignored.
    query = "&".join(f"{k}={httpx.QueryParams({k: v}).get(k)}" for k, v in params.items())
    return f"{cfg.auth_url}?{query}"


def exchange_code(
    session: Session,
    provider_key: str,
    state: str,
    code: str,
    *,
    http: Any | None = None,
) -> ServiceConnection:
    """Exchange the OAuth ``code`` for an access token; store it as a ServiceConnection."""
    cfg = PROVIDERS.get(provider_key)
    if cfg is None:
        raise ValueError(f"unknown provider {provider_key!r}")
    cfg.assert_configured()

    user_id = _consume_state(state, provider_key)

    body = {
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": cfg.redirect_uri,
    }
    client = http or httpx.Client(timeout=30)
    resp = client.post(cfg.token_url, data=body)
    resp.raise_for_status()
    payload = resp.json()

    access_token = (
        payload.get("access_token")
        or payload.get("authed_user", {}).get("access_token")  # Slack tucks the user token here
    )
    if not access_token:
        raise RuntimeError(f"oauth callback returned no access_token: {payload!r}")

    # Look up the user the state token claimed
    from .db import User

    user = session.get(User, user_id)
    if user is None:
        raise ValueError(f"user {user_id} not found")

    # Slack's bot-token flow specifically returns a bot token at top level too;
    # we prefer that over the user token for our use case.
    if provider_key == "slack_bot":
        access_token = payload.get("access_token") or access_token

    return store_connection(session, user, provider_key, access_token)
