"""Feed fetcher."""

from __future__ import annotations

from fetcher.client import FetchResult, fetch
from fetcher.config import FetchConfig, from_env

__all__ = ["FetchConfig", "FetchResult", "fetch", "from_env"]
