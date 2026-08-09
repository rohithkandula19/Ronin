"""Read a ``.env`` file into a mapping."""

from __future__ import annotations


def parse_env(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines, honouring comments and quoted values."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out
