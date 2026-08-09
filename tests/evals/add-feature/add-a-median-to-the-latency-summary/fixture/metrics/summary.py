"""Summary statistics for the latency samples collected per endpoint.

An endpoint that has had no traffic in the window is normal, not an error, so
the empty case returns the same keys with None rather than raising: the caller
renders a dashboard row either way.
"""

from __future__ import annotations


def summarize(values):
    """Return count/min/max/mean for *values*, with None stats when empty."""
    samples = [float(value) for value in values]
    if not samples:
        return {"count": 0, "min": None, "max": None, "mean": None}
    return {
        "count": len(samples),
        "min": min(samples),
        "max": max(samples),
        "mean": sum(samples) / len(samples),
    }
