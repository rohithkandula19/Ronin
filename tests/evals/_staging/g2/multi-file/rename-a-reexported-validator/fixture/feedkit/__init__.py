"""feedkit -- a very small feed ingestion library."""

from __future__ import annotations

from feedkit.validate import FeedError, check_feed_payload

__all__ = ["FeedError", "check_feed_payload"]
