"""Config file management for csk.

Config lives at ``./.csk/config.toml`` (project-local, takes priority) or
``~/.config/csk/config.toml`` (user-global). Demo mode skips all real creds.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, Field, model_validator


CONFIG_FILENAME = "config.toml"
PROJECT_DIR = Path(".csk")
USER_DIR = Path.home() / ".config" / "csk"


PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "anthropic": {"model": "claude-sonnet-4-6", "base_url": ""},
    "ollama": {"model": "llama3.1", "base_url": "http://localhost:11434/v1"},
    "openai": {"model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1"},
    "together": {"model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "base_url": "https://api.together.xyz/v1"},
    "groq": {"model": "llama-3.3-70b-versatile", "base_url": "https://api.groq.com/openai/v1"},
    "fireworks": {"model": "accounts/fireworks/models/llama-v3p3-70b-instruct", "base_url": "https://api.fireworks.ai/inference/v1"},
    # Free tiers, no credit card — a real quality bump over llama at $0:
    "gemini": {"model": "gemini-2.0-flash", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"},
    "cerebras": {"model": "llama-3.3-70b", "base_url": "https://api.cerebras.ai/v1"},
    # OpenRouter: one key, many models. Default is a strong free, tool-calling
    # model; override with `--model` (e.g. qwen/qwen3-coder:free).
    "openrouter": {"model": "deepseek/deepseek-v4-flash:free", "base_url": "https://openrouter.ai/api/v1"},
}


class CSKConfig(BaseModel):
    """csk configuration. Each service is optional — only configured ones get tools registered."""

    demo_mode: bool = False
    provider: str = "anthropic"  # anthropic | ollama | openai | together | groq | fireworks | gemini | cerebras | openrouter | custom
    model: str | None = None  # None → use the preset default for the chosen provider
    base_url: str | None = None  # required for 'custom'; optional override otherwise

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None  # used for openai/together/groq/fireworks/custom

    stripe_api_key: str | None = None
    linear_api_key: str | None = None
    slack_bot_token: str | None = None
    slack_user_token: str | None = None
    notion_token: str | None = None
    database_url: str | None = None

    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _accept_api_key_alias(cls, data: Any) -> Any:
        """Allow a provider-neutral ``api_key`` in the TOML as an alias for
        ``openai_api_key`` — the latter name is confusing for Groq/Together/etc.
        users. ``openai_api_key`` still works; ``api_key`` just maps onto it."""
        if isinstance(data, dict) and data.get("api_key") and not data.get("openai_api_key"):
            data = {**data, "openai_api_key": data["api_key"]}
        return data

    def resolved_model(self) -> str:
        if self.model:
            return self.model
        return PROVIDER_PRESETS.get(self.provider, {}).get("model", "claude-sonnet-4-6")

    def resolved_base_url(self) -> str | None:
        if self.base_url:
            return self.base_url
        preset = PROVIDER_PRESETS.get(self.provider, {}).get("base_url", "")
        return preset or None

    def configured_services(self) -> list[str]:
        """Names of services that have credentials set (or are available in demo mode)."""
        if self.demo_mode:
            return ["stripe", "linear", "slack", "notion", "postgres"]
        services: list[str] = []
        if self.stripe_api_key:
            services.append("stripe")
        if self.linear_api_key:
            services.append("linear")
        if self.slack_bot_token:
            services.append("slack")
        if self.notion_token:
            services.append("notion")
        if self.database_url:
            services.append("postgres")
        return services

    def has_provider_auth(self) -> bool:
        """Returns True iff the chosen provider has the credentials it needs."""
        if self.demo_mode:
            return True
        if self.provider == "anthropic":
            return bool(self.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"))
        if self.provider == "ollama":
            return True  # local server, no auth
        # openai-compat needs a key
        return bool(self.openai_api_key or os.environ.get("OPENAI_API_KEY"))

    # Backward-compat alias used by older code paths.
    def has_anthropic_auth(self) -> bool:
        return self.has_provider_auth()


def find_config_path() -> Path | None:
    """Return the first existing config path: project, then user. None if neither exists."""
    project = PROJECT_DIR / CONFIG_FILENAME
    if project.exists():
        return project
    user = USER_DIR / CONFIG_FILENAME
    if user.exists():
        return user
    return None


def load_config() -> CSKConfig:
    """Load config from disk. Returns an empty config if no file exists.

    Env vars override file values for keys that aren't None in env:
    ``ANTHROPIC_API_KEY``, ``STRIPE_API_KEY``, ``LINEAR_API_KEY``,
    ``SLACK_BOT_TOKEN``, ``NOTION_TOKEN``, ``DATABASE_URL``.
    """
    path = find_config_path()
    raw: dict[str, Any] = {}
    if path is not None:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)

    # env override
    env_overrides = {
        "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY"),
        "openai_api_key": os.environ.get("OPENAI_API_KEY"),
        "stripe_api_key": os.environ.get("STRIPE_API_KEY"),
        "linear_api_key": os.environ.get("LINEAR_API_KEY"),
        "slack_bot_token": os.environ.get("SLACK_BOT_TOKEN"),
        "slack_user_token": os.environ.get("SLACK_USER_TOKEN"),
        "notion_token": os.environ.get("NOTION_TOKEN"),
        "database_url": os.environ.get("DATABASE_URL"),
    }
    for key, value in env_overrides.items():
        if value:
            raw[key] = value

    return CSKConfig(**raw)


def save_config(config: CSKConfig, *, scope: str = "project") -> Path:
    """Write config to disk. ``scope`` is ``"project"`` or ``"user"``."""
    target_dir = PROJECT_DIR if scope == "project" else USER_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / CONFIG_FILENAME

    payload = config.model_dump(exclude_none=False)
    payload = {k: v for k, v in payload.items() if v not in (None, {}, "")}
    with path.open("wb") as fh:
        tomli_w.dump(payload, fh)
    return path
