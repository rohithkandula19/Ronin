"""Transports that wrap other transports."""

from __future__ import annotations

from fetchkit.backends.cached import CachingTransport

__all__ = ["CachingTransport"]
