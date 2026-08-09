"""Retrying a GET while the server is unhappy."""

from __future__ import annotations

from fetchkit.client import Client
from fetchkit.transport import DEFAULT_TIMEOUT, Response

RETRYABLE = frozenset({500, 502, 503, 504})


def get_with_retry(
    client: Client,
    path: str,
    *,
    attempts: int = 3,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Response:
    """GET *path*, retrying while the status is retryable. Returns the last try."""
    response = Response(0, "never sent")
    for _ in range(max(1, attempts)):
        response = client.get(path, headers=headers, timeout=timeout)
        if response.status not in RETRYABLE:
            return response
    return response
