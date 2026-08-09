"""Render two tables: one populated, one empty."""

from __future__ import annotations

from tabular.render import render


def main() -> None:
    print(render([("a", "1"), ("b", "2")]))
    print(render([]))
