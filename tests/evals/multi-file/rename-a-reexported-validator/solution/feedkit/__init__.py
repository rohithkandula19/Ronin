"""feedkit -- a very small feed ingestion library."""

from __future__ import annotations

from feedkit.validate import FeedError, collect_payload_problems

__all__ = ["FeedError", "collect_payload_problems"]
