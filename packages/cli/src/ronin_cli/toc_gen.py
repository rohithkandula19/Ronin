"""Generate a Markdown table of contents — `ronin toc`.

Reads Markdown, finds the headings (ignoring fenced code blocks), and builds a
nested TOC with GitHub-style anchor links. Pure and unit-tested.
"""
from __future__ import annotations

import re

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")


def extract_headings(md: str) -> list[tuple[int, str]]:
    """(level, text) for each heading, skipping fenced code blocks. Pure."""
    out: list[tuple[int, str]] = []
    in_code = False
    for line in md.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = _HEADING.match(line)
        if m:
            out.append((len(m.group(1)), m.group(2).strip()))
    return out


def anchor(text: str) -> str:
    """GitHub-style anchor slug for a heading. Pure."""
    s = text.lower()
    s = re.sub(r"[^\w\s-]", "", s)        # drop punctuation/emoji
    return s.strip().replace(" ", "-")    # GitHub replaces each space (keeps doubles)


def generate_toc(headings: list[tuple[int, str]], *, min_level: int = 2,
                 max_level: int = 4) -> str:
    """Build a nested bullet TOC from headings. Pure."""
    lines: list[str] = []
    base = min(((lvl for lvl, _ in headings if lvl >= min_level)), default=min_level)
    for level, text in headings:
        if level < min_level or level > max_level:
            continue
        indent = "  " * (level - base)
        lines.append(f"{indent}- [{text}](#{anchor(text)})")
    return "\n".join(lines)
