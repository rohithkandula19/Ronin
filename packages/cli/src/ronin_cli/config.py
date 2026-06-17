"""Config file management for csk.

Config lives at ``./.ronin/config.toml`` (project-local, takes priority) or
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
PROJECT_DIR = Path(".ronin")
USER_DIR = Path.home() / ".config" / "ronin"
# Legacy dirs from the "csk / Claude kit" era — migrated to the ronin names on
# first load so existing users keep their config, sessions, and skills.
LEGACY_PROJECT_DIR = Path(".csk")
LEGACY_USER_DIR = Path.home() / ".config" / "csk"


def _merge_move(old: Path, new: Path) -> None:
    """Recursively move ``old`` into ``new`` without clobbering. If ``new``
    doesn't exist, move wholesale; if both are dirs, merge child-by-child; if a
    file already exists at the destination, the legacy copy is left in place."""
    import shutil
    if not new.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(new))
        return
    if old.is_dir() and new.is_dir():
        for child in list(old.iterdir()):
            _merge_move(child, new / child.name)
        if not any(old.iterdir()):
            old.rmdir()


def migrate_legacy_dirs() -> None:
    """One-time migration of legacy .csk → .ronin (project, in cwd) and
    ~/.config/csk → ~/.config/ronin, recursively merging so existing data is
    never clobbered (and a .ronin created first doesn't strand old sessions).
    Best-effort and silent on failure."""
    for old, new in ((LEGACY_PROJECT_DIR, PROJECT_DIR), (LEGACY_USER_DIR, USER_DIR)):
        try:
            if old.exists():
                _merge_move(old, new)
        except OSError:
            pass


PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "anthropic": {"model": "claude-sonnet-4-6", "base_url": ""},
    "ollama": {"model": "llama3.1", "base_url": "http://localhost:11434/v1"},
    "openai": {"model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1"},
    "together": {"model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "base_url": "https://api.together.xyz/v1"},
    "groq": {"model": "llama-3.3-70b-versatile", "base_url": "https://api.groq.com/openai/v1"},
    "fireworks": {"model": "accounts/fireworks/models/llama-v3p3-70b-instruct", "base_url": "https://api.fireworks.ai/inference/v1"},
    # Free tiers, no credit card — a real quality bump over llama at $0:
    "gemini": {"model": "gemini-flash-latest", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"},
    "cerebras": {"model": "gpt-oss-120b", "base_url": "https://api.cerebras.ai/v1"},
    # OpenRouter: one key, many models. Default is a strong free, tool-calling
    # model; override with `--model` (e.g. qwen/qwen3-coder:free).
    "openrouter": {"model": "deepseek/deepseek-v4-flash:free", "base_url": "https://openrouter.ai/api/v1"},
}


class RoninConfig(BaseModel):
    """csk configuration. Each service is optional — only configured ones get tools registered."""

    demo_mode: bool = False
    provider: str = "anthropic"  # anthropic | ollama | openai | together | groq | fireworks | gemini | cerebras | openrouter | custom
    model: str | None = None  # None → use the preset default for the chosen provider
    base_url: str | None = None  # required for 'custom'; optional override otherwise
    # Smart routing: when both are set, ronin picks the cheap/fast model for
    # simple turns and the strong model for complex ones (same provider).
    route_fast: str | None = None
    route_strong: str | None = None
    # Self-verification: after a turn that made changes, the agent reviews its own
    # work against the request and fixes gaps (Reflexion-style). Opt-in via /verify.
    verify: bool = False
    # Sentinel mode: the agent declares CONFIDENCE: high|medium|low and abstains
    # over bluffing. Low confidence on a routed cheap blade triggers escalation.
    sentinel: bool = False
    # Spend cap (USD) for an interactive session: ronin tracks estimated cost and
    # warns once the session crosses it. None → no cap.
    budget: float | None = None
    # Fast mode: ship only the core coding tools (smaller per-call payload →
    # snappier turns + fewer rate-limit hits). Trades breadth for speed.
    fast: bool = False
    # Cross-provider failover: ordered fallbacks tried (after the primary) when a
    # provider rate-limits or errors mid-turn. Each entry is a partial provider
    # spec: {provider, model?, base_url?, api_key?}. Empty → no failover.
    failover: list[dict[str, Any]] = Field(default_factory=list)
    # Offline mode: force a local brain and forbid any network egress (web tools
    # off, only a local provider allowed). Nothing leaves the machine.
    offline: bool = False
    # Auto context engineering: each interactive turn, inject a compact block of
    # the most relevant files (from the repo map) so the model starts aimed at
    # the right code. Non-blocking; set False to disable.
    auto_context: bool = True
    # Full-access mode (opt-in, `--full-access`): lifts the guards — filesystem
    # access beyond the project root, auto-approve (no y/n gate), longer command
    # timeouts + bigger output caps. Powerful and unsandboxed; off by default.
    full_access: bool = False
    # Faithfulness / grounding harness mode. After an agent answer (and on a
    # proposed edit), the harness checks whether the output is grounded in the
    # sources the agent actually read:
    #   "off"  — never run the harness (default; zero overhead).
    #   "warn" — run it and surface a faithfulness score + ungrounded claims /
    #            hallucinated symbols as a non-blocking warning.
    #   "gate" — run it and, when the answer is ungrounded or the harness
    #            abstains, hold the answer / edit for confirmation.
    faithfulness: str = "off"  # off | warn | gate

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None  # legacy shared slot (openai/together/groq/…)
    # Per-provider keys — keyed by provider name. This is what prevents a
    # `/login openai` from clobbering your cerebras key: each provider keeps its
    # own. ``key_for`` prefers this over the legacy flat fields above.
    provider_keys: dict[str, str] = Field(default_factory=dict)

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

    @model_validator(mode="after")
    def _normalize_faithfulness(self) -> "RoninConfig":
        """Normalize and validate the faithfulness mode. An unknown value falls
        back to ``"off"`` rather than crashing config load (a typo in the TOML
        should not brick the CLI)."""
        mode = (self.faithfulness or "off").strip().lower()
        if mode not in ("off", "warn", "gate"):
            mode = "off"
        self.faithfulness = mode
        return self

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

    def key_for(self, provider: str | None = None) -> str | None:
        """The API key to use for ``provider`` (defaults to the active one).

        Resolution order: the per-provider store (``provider_keys``) wins, then
        the legacy flat field for that provider family, then the env var. This is
        what lets several providers keep their own keys at once."""
        provider = provider or self.provider
        if provider in self.provider_keys and self.provider_keys[provider]:
            return self.provider_keys[provider]
        if provider == "anthropic":
            return self.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        if provider == "ollama":
            return None  # local, no key
        return self.openai_api_key or os.environ.get("OPENAI_API_KEY")

    def set_key_for(self, provider: str, key: str) -> None:
        """Store ``key`` for ``provider`` without disturbing other providers'
        keys. Also mirrors into the legacy flat field for the current provider so
        older code paths still resolve it."""
        self.provider_keys[provider] = key
        if provider == "anthropic":
            self.anthropic_api_key = key
        else:
            self.openai_api_key = key

    def has_provider_auth(self) -> bool:
        """Returns True iff the chosen provider has the credentials it needs."""
        if self.demo_mode:
            return True
        if self.provider == "ollama":
            return True  # local server, no auth
        return bool(self.key_for())

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


def load_config() -> RoninConfig:
    """Load config from disk. Returns an empty config if no file exists.

    Env vars override file values for keys that aren't None in env:
    ``ANTHROPIC_API_KEY``, ``STRIPE_API_KEY``, ``LINEAR_API_KEY``,
    ``SLACK_BOT_TOKEN``, ``NOTION_TOKEN``, ``DATABASE_URL``.
    """
    migrate_legacy_dirs()  # carry .csk → .ronin forward (one-time, silent)
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

    return RoninConfig(**raw)


def save_config(config: RoninConfig, *, scope: str = "project") -> Path:
    """Write config to disk. ``scope`` is ``"project"`` or ``"user"``."""
    target_dir = PROJECT_DIR if scope == "project" else USER_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / CONFIG_FILENAME

    payload = config.model_dump(exclude_none=False)
    payload = {k: v for k, v in payload.items() if v not in (None, {}, "")}
    with path.open("wb") as fh:
        tomli_w.dump(payload, fh)
    return path
