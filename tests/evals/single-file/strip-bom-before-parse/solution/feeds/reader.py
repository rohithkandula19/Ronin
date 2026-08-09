"""Read the flat ``key=value`` feed descriptors ops hand us."""

from __future__ import annotations

_BOM = "\ufeff"


def parse_kv(text: str) -> dict[str, str]:
    """Parse ``key=value`` lines. Blank lines and ``#`` comments are skipped."""
    if text.startswith(_BOM):
        text = text[len(_BOM) :]
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition("=")
        out[key.strip()] = value.strip()
    return out
