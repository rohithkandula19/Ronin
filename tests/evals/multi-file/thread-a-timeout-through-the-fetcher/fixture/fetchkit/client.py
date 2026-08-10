"""The thing application code holds on to."""

from __future__ import annotations

from fetchkit.transport import Request, Response, Transport


class Client:
    """A very small HTTP-ish client over a Transport."""

    def __init__(self, transport: Transport, *, base_headers: dict[str, str] | None = None) -> None:
        self.transport = transport
        self.base_headers = dict(base_headers or {})

    def request(self, method: str, path: str, *, headers: dict[str, str] | None = None) -> Response:
        merged = {**self.base_headers, **(headers or {})}
        request = Request(method, path, tuple(sorted(merged.items())))
        return self.transport.send(request)

    def get(self, path: str, *, headers: dict[str, str] | None = None) -> Response:
        return self.request("GET", path, headers=headers)

    def post(self, path: str, *, headers: dict[str, str] | None = None) -> Response:
        return self.request("POST", path, headers=headers)
