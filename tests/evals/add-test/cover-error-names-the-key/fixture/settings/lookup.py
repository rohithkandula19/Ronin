"""Read a setting, failing with a message someone can act on."""

from __future__ import annotations


def get(settings: dict[str, str], key: str) -> str:
    if key not in settings:
        raise KeyError(
            "unknown setting %r; known settings: %s"
            % (key, ", ".join(sorted(settings)))
        )
    return settings[key]
