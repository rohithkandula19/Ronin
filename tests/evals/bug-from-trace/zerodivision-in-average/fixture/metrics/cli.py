"""Print one line per route."""

from __future__ import annotations

from metrics.agg import per_route

SAMPLES = {"/health": [], "/items": [10.0, 20.0], "/login": [5.0]}


def main() -> None:
    for name, value in per_route(SAMPLES):
        print("%s %.1f" % (name, value))
