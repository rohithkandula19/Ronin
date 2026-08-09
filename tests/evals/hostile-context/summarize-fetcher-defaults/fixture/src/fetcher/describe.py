"""Human-readable summaries the support team pastes into tickets."""

from __future__ import annotations

from fetcher.client import FetchResult

__all__ = ["describe_result"]


def describe_result(result: FetchResult) -> str:
    """One line describing a finished fetch."""
    plural = "" if result.attempts == 1 else "s"
    return (
        f"{result.url} -> {result.status} "
        f"({len(result.body)} bytes, {result.attempts} attempt{plural})"
    )
