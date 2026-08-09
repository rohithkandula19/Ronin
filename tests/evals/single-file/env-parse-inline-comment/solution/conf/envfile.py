"""Read a ``.env`` file into a mapping."""

from __future__ import annotations


def _strip_comment(value: str) -> str:
    if value.startswith("#"):
        return ""
    for index in range(1, len(value)):
        if value[index] == "#" and value[index - 1] in " \t":
            return value[:index]
    return value


def parse_env(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines, honouring comments and quoted values."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            out[key.strip()] = value[1:-1]
            continue
        out[key.strip()] = _strip_comment(value).strip()
    return out
