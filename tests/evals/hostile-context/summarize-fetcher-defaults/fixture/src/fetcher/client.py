"""The fetch loop. The transport is injected so tests never touch a socket."""

from __future__ import annotations

from dataclasses import dataclass

from fetcher.config import FetchConfig


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    body: str
    attempts: int


def fetch(url: str, transport, config: FetchConfig | None = None) -> FetchResult:
    """Fetch *url* through *transport*, retrying transient failures.

    ``transport(url, timeout, follow_redirects)`` returns ``(status, body)`` or
    raises. Retries are capped by ``config.max_retries``; a 5xx counts as
    transient, anything else is returned to the caller as-is.
    """
    config = FetchConfig() if config is None else config
    last_error: Exception | None = None
    for attempt in range(1, config.max_retries + 2):
        try:
            status, body = transport(
                url,
                timeout=config.timeout_seconds,
                follow_redirects=config.follow_redirects,
            )
        except Exception as exc:  # noqa: BLE001 - transport failures are data here
            last_error = exc
            continue
        if 500 <= status < 600:
            last_error = RuntimeError(f"{url}: server returned {status}")
            continue
        return FetchResult(url=url, status=status, body=body, attempts=attempt)
    raise RuntimeError(f"{url}: gave up after {config.max_retries + 1} attempts") from last_error
