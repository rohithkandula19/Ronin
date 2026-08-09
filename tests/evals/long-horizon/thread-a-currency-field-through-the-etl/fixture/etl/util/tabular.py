"""Fixed-width table rendering for the human-readable summary."""

from __future__ import annotations

from typing import Sequence


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = [_render_row(headers, widths), _render_row(["-" * w for w in widths], widths)]
    lines.extend(_render_row(row, widths) for row in rows)
    return "\n".join(lines)


def _render_row(cells: Sequence[str], widths: Sequence[int]) -> str:
    return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths)).rstrip()
