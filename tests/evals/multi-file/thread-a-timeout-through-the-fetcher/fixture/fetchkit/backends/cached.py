"""A transport that remembers successful GETs."""

from __future__ import annotations

from fetchkit.transport import Request, Response, Transport


class CachingTransport(Transport):
    """Wrap *inner* and serve repeated successful GETs from memory."""

    def __init__(self, inner: Transport) -> None:
        self.inner = inner
        self.cache: dict[tuple[str, str], Response] = {}
        self.hits = 0

    def send(self, request: Request) -> Response:
        key = (request.method, request.path)
        if request.method == "GET" and key in self.cache:
            self.hits += 1
            return self.cache[key]
        response = self.inner.send(request)
        if request.method == "GET" and response.status == 200:
            self.cache[key] = response
        return response
