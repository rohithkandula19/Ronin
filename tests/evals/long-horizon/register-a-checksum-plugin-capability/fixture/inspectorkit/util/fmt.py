"""Rendering helpers."""

from __future__ import annotations

from typing import Mapping, Sequence

UNITS = ("B", "KiB", "MiB", "GiB")


def human_size(count: int) -> str:
    size = float(count)
    for unit in UNITS:
        if size < 1024 or unit == UNITS[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def render_mapping(values: Mapping[str, object], indent: str = "    ") -> str:
    if not values:
        return f"{indent}(nothing)"
    width = max(len(key) for key in values)
    return "\n".join(f"{indent}{key.ljust(width)}  {value}" for key, value in values.items())


def render_counts(values: Mapping[str, int], indent: str = "    ", top: int = 5) -> str:
    if not values:
        return f"{indent}(nothing)"
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))[:top]
    width = max(len(key) for key, _ in ordered)
    return "\n".join(f"{indent}{key.ljust(width)}  {count}" for key, count in ordered)


def render_lines(values: Sequence[str], indent: str = "    ") -> str:
    if not values:
        return f"{indent}(none)"
    return "\n".join(f"{indent}- {value}" for value in values)
