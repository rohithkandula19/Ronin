"""Read the flat ``key=value`` feed descriptors ops hand us."""

from __future__ import annotations


def parse_kv(text: str) -> dict[str, str]:
    """Parse ``key=value`` lines. Blank lines and ``#`` comments are skipped."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition("=")
        out[key.strip()] = value.strip()
    return out
