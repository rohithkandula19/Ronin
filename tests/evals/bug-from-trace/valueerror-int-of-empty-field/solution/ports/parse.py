"""Parse the comma-separated port allow-list from the site config.

Operators leave trailing commas and stray spaces in this field constantly,
so empty entries are ignored rather than rejected.
"""

from __future__ import annotations


def parse_ports(text: str) -> list[int]:
    out = []
    for part in text.split(","):
        stripped = part.strip()
        if stripped:
            out.append(int(stripped))
    return out
