"""Parse the ``key=value`` configuration text."""

from __future__ import annotations

from taskq.errors import ConfigError

KNOWN_KEYS = frozenset({"max_attempts", "queue", "default_priority"})
DEFAULTS = {"max_attempts": "3", "queue": "default", "default_priority": "0"}


def parse_config(text: str) -> dict[str, str]:
    """Defaults overlaid with ``text``. An unknown key is an error."""
    out = dict(DEFAULTS)
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError("line %d is not key=value: %r" % (number, raw))
        key, _, value = line.partition("=")
        key = key.strip()
        if key not in KNOWN_KEYS:
            raise ConfigError(
                "unknown config key %r; known keys: %s"
                % (key, ", ".join(sorted(KNOWN_KEYS)))
            )
        out[key] = value.strip()
    return out
