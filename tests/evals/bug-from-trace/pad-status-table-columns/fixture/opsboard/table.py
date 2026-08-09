"""Turns service records into the plain-text board we print to the terminal."""

from __future__ import annotations

from collections.abc import Sequence

from opsboard.layout import pad_cell, rule
from opsboard.settings import Settings

HEADERS = ("service", "region", "state")


def format_row(cells: Sequence[str], settings: Settings) -> str:
    return settings.separator.join(pad_cell(cell, settings.column_width) for cell in cells)


def render(records: Sequence[dict[str, str]], settings: Settings) -> str:
    lines = [format_row(HEADERS, settings)]
    if settings.show_rule:
        lines.append(rule(settings.column_width, len(HEADERS), settings.separator))
    for record in records:
        lines.append(format_row([record[key] for key in HEADERS], settings))
    return "\n".join(lines)
