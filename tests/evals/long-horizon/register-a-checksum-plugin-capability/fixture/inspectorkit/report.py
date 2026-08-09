"""Render dispatch results as the text report ops reads.

Sections appear in the order given by ``report_order`` in the config, and a
capability that is not listed there is not rendered at all: the report layout is
an operational decision, not a code one.
"""

from __future__ import annotations

from typing import Sequence

from .capabilities import capability
from .dispatch import Result
from .util.fmt import render_counts, render_lines, render_mapping


def render(
    blob_name: str,
    results: Sequence[Result],
    order: Sequence[str],
    top: int = 5,
) -> str:
    lines: list[str] = [f"# {blob_name}"]
    for capability_name in order:
        section = [r for r in results if r.capability == capability_name]
        if not section:
            continue
        lines.append("")
        lines.append(f"## {capability_name}")
        for result in section:
            lines.append(f"  {result.plugin}:")
            lines.append(_render_value(capability_name, result.value, top=top))
    return "\n".join(lines) + "\n"


def _render_value(capability_name: str, value: object, top: int) -> str:
    kind = capability(capability_name).result_kind
    if kind == "mapping":
        return render_mapping(value, indent="      ")  # type: ignore[arg-type]
    if kind == "counts":
        return render_counts(value, indent="      ", top=top)  # type: ignore[arg-type]
    if kind == "lines":
        return render_lines(value, indent="      ")  # type: ignore[arg-type]
    return f"      {value}"
