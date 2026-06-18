"""Fully offline mode — a coding agent that runs with zero network egress.

Claude Code (and every cloud agent) needs a round-trip to a remote API for every
turn. ronin can run its brain locally (Ollama / any localhost OpenAI-compatible
server), so it can work on a plane, in an air-gapped environment, or when you
simply don't want a line of your code leaving the machine.

Offline mode enforces two guarantees:
1. **Local brain only.** The provider must be local; a cloud provider is switched
   to Ollama (or rejected). ``is_local_provider`` decides what counts as local.
2. **No network tools.** Web search / fetch / remote-image tools are stripped, so
   the agent can't reach out even if it wanted to.
"""
from __future__ import annotations

from .config import RoninConfig

# Tools that touch the network — removed from the toolbelt in offline mode.
NETWORK_TOOLS: frozenset[str] = frozenset({
    "web_search", "fetch_url", "generate_image",
    # Optional Playwright browser tools (web computer-use) also egress.
    "browse_navigate", "browse_click", "browse_type",
    "browse_read", "browse_screenshot",
})

# Hosts that count as "on this machine".
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def is_local_provider(config: RoninConfig) -> bool:
    """True if the configured provider runs on this machine (no egress)."""
    if config.provider == "ollama":
        return True
    base = (config.resolved_base_url() or "").lower()
    return any(host in base for host in _LOCAL_HOSTS)


def apply_offline(config: RoninConfig) -> RoninConfig:
    """Return a config safe to run offline.

    If the provider is already local, it's kept as-is. Otherwise we switch to a
    local Ollama brain so the agent still works — nothing leaves the machine.
    Failover is also cleared (cloud fallbacks would defeat the guarantee)."""
    if not config.offline:
        return config
    if is_local_provider(config):
        return config.model_copy(update={"failover": []})
    return config.model_copy(update={
        "provider": "ollama",
        "model": config.model if config.provider == "ollama" else None,
        "base_url": None,
        "failover": [],
    })


def strip_network_tools(tools: list) -> list:
    """Drop any network-touching tools (used when offline)."""
    return [t for t in tools if getattr(t, "name", None) not in NETWORK_TOOLS]
