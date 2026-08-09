"""The transfer worker loop.

The sleeper is injected so the tests do not spend real wall-clock time.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from .retry import DownloadRetryPolicy, UploadRetryPolicy


def run_upload(
    send: Callable[[bytes], int],
    payload: bytes,
    *,
    policy: UploadRetryPolicy | None = None,
    sleep: Callable[[float], None] = lambda _seconds: None,
) -> int:
    """Send `payload`, retrying on retryable statuses. Returns the last status."""
    active = policy or UploadRetryPolicy()
    status = 0
    for attempt in range(1, active.max_attempts + 1):
        status = send(payload)
        if not active.should_retry(attempt, status):
            return status
        sleep(active.next_delay(attempt))
    return status


def run_download(
    fetch: Callable[[int], tuple[int, bytes]],
    ranges: Iterator[int],
    *,
    policy: DownloadRetryPolicy | None = None,
    sleep: Callable[[float], None] = lambda _seconds: None,
) -> list[bytes]:
    """Fetch each range, retrying on retryable statuses."""
    active = policy or DownloadRetryPolicy()
    chunks: list[bytes] = []
    for offset in ranges:
        for attempt in range(1, active.max_attempts + 1):
            status, chunk = fetch(offset)
            if not active.should_retry(attempt, status):
                chunks.append(chunk)
                break
            sleep(active.next_delay(attempt))
    return chunks
