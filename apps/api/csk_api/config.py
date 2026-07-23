"""Runtime settings. Loaded from env vars, validated by pydantic-settings."""
from __future__ import annotations

import os
import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_fernet_key() -> str:
    """For dev/tests only. Production deploys MUST set FERNET_KEY explicitly."""
    return os.environ.get("FERNET_KEY") or "JeR8x2a1lO0xz_q-rA9bC8d4e5f6g7h8i9j0k1l2m3n="


class Settings(BaseSettings):
    """Settings for the SaaS API."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./csk_saas.db"
    fernet_key: str = _default_fernet_key()
    api_token_bytes: int = 24  # length of generated per-user API tokens
    default_briefing_provider: str = "anthropic"
    default_briefing_model: str = "claude-sonnet-4-6"
    enable_scheduler: bool = True
    debug: bool = False
    # Comma-separated exact origins allowed to call the API cross-origin from a
    # browser (e.g. "https://app.example.com,https://staging.example.com").
    # Empty (default) = no CORS headers emitted — same-origin / server-proxy only.
    # Never use "*" with credentials; list the real web origins.
    cors_origins: str = ""

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    """Memoized accessor. Tests override via dependency injection in main.py."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_for_tests() -> None:
    global _settings
    _settings = None


def generate_api_token(n_bytes: int = 24) -> str:
    """URL-safe per-user token. Stored hashed in the DB."""
    return f"csk_{secrets.token_urlsafe(n_bytes)}"
