"""fetchkit -- a tiny client with pluggable transports."""

from __future__ import annotations

from fetchkit.backends import CachingTransport
from fetchkit.client import Client
from fetchkit.retry import get_with_retry
from fetchkit.transport import DEFAULT_TIMEOUT, FakeTransport, Request, Response, Transport

__all__ = [
    "DEFAULT_TIMEOUT",
    "CachingTransport",
    "Client",
    "FakeTransport",
    "Request",
    "Response",
    "Transport",
    "get_with_retry",
]
