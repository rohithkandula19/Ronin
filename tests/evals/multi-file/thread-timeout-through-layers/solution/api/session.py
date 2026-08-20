"""Adds the base URL and keeps the transport."""

from __future__ import annotations

from api.transport import Transport


class Session:
    def __init__(self, base: str, transport: Transport) -> None:
        self.base = base.rstrip("/")
        self.transport = transport

    def request(self, path: str, timeout: float | None = None) -> str:
        url = self.base + "/" + path.lstrip("/")
        if timeout is None:
            return self.transport.send(url)
        return self.transport.send(url, timeout=timeout)
