"""Summary statistics for the latency samples collected per endpoint.

An endpoint that has had no traffic in the window is normal, not an error, so
the empty case returns the same keys with None rather than raising: the caller
renders a dashboard row either way.
"""

from __future__ import annotations


def _median(sorted_samples):
    """Middle value, or the mean of the two middle values for an even count."""
    count = len(sorted_samples)
    middle = count // 2
    if count % 2:
        return sorted_samples[middle]
    return (sorted_samples[middle - 1] + sorted_samples[middle]) / 2


def summarize(values):
    """Return count/min/max/mean/median for *values*, with None stats when empty."""
    samples = sorted(float(value) for value in values)
    if not samples:
        return {"count": 0, "min": None, "max": None, "mean": None, "median": None}
    return {
        "count": len(samples),
        "min": samples[0],
        "max": samples[-1],
        "mean": sum(samples) / len(samples),
        "median": _median(samples),
    }
