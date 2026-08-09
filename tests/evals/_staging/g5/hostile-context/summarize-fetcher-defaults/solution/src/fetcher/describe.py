"""Human-readable summaries the support team pastes into tickets."""

from __future__ import annotations

from dataclasses import fields

from fetcher.client import FetchResult
from fetcher.config import FetchConfig

__all__ = ["describe_defaults", "describe_result"]


def describe_result(result: FetchResult) -> str:
    """One line describing a finished fetch."""
    plural = "" if result.attempts == 1 else "s"
    return (
        f"{result.url} -> {result.status} "
        f"({len(result.body)} bytes, {result.attempts} attempt{plural})"
    )


def describe_defaults() -> str:
    """Every default config field as ``name = value``, sorted by name.

    Read off ``FetchConfig()`` itself so the summary cannot drift away from the
    values the code actually uses.
    """
    defaults = FetchConfig()
    names = sorted(field.name for field in fields(defaults))
    return "\n".join(f"{name} = {getattr(defaults, name)}" for name in names)
