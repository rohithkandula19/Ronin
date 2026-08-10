"""Per-route latency aggregation.

A route that was registered but never called has no samples. Its mean is
reported as 0.0 rather than being an error.
"""

from __future__ import annotations


def mean(samples: list[float]) -> float:
    return sum(samples) / len(samples)


def per_route(routes: dict[str, list[float]]) -> list[tuple[str, float]]:
    return [(name, mean(routes[name])) for name in sorted(routes)]
