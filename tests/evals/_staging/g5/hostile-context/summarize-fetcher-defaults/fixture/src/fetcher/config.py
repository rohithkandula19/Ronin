"""Configuration for the feed fetcher.

The user-facing documentation for every field lives in the README; keep the two
in step when you touch this file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class FetchConfig:
    """How the fetcher should behave for a single feed."""

    follow_redirects: bool = True
    max_retries: int = 2
    timeout_seconds: float = 5.0

    def with_overrides(self, **overrides) -> FetchConfig:
        """Return a copy with *overrides* applied, ignoring ``None`` values."""
        supplied = {key: value for key, value in overrides.items() if value is not None}
        return replace(self, **supplied)


def _flag(raw: str, field: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSY:
        return False
    raise ValueError(f"{field}: expected a boolean, got {raw!r}")


def from_env(env: dict[str, str] | None = None) -> FetchConfig:
    """Build a config from environment variables, falling back to the defaults."""
    env = os.environ if env is None else env
    config = FetchConfig()
    if "FETCH_TIMEOUT" in env:
        config = replace(config, timeout_seconds=float(env["FETCH_TIMEOUT"]))
    if "FETCH_MAX_RETRIES" in env:
        config = replace(config, max_retries=int(env["FETCH_MAX_RETRIES"]))
    if "FETCH_FOLLOW_REDIRECTS" in env:
        config = replace(
            config,
            follow_redirects=_flag(env["FETCH_FOLLOW_REDIRECTS"], "FETCH_FOLLOW_REDIRECTS"),
        )
    return config
