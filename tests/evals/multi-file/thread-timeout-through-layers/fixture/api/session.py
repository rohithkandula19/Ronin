"""Adds the base URL and keeps the transport."""

from __future__ import annotations

from api.transport import Transport


class Session:
    def __init__(self, base: str, transport: Transport) -> None:
        self.base = base.rstrip("/")
        self.transport = transport

    def request(self, path: str) -> str:
        return self.transport.send(self.base + "/" + path.lstrip("/"))
