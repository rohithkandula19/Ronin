"""Transports: the layer that actually puts a request on the wire."""

from __future__ import annotations

from dataclasses import dataclass, field

# Seconds. Every request currently waits exactly this long before giving up.
DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Response:
    status: int
    body: str = ""


class Transport:
    """Base class. A transport turns a Request into a Response."""

    def send(self, request: Request) -> Response:
        raise NotImplementedError


@dataclass
class FakeTransport(Transport):
    """Offline transport used by the demo and the tests.

    *responses* maps a path to the Response to hand back; *latency* says how many
    seconds a path pretends to take, so a request can time out.
    """

    responses: dict[str, Response] = field(default_factory=dict)
    latency: dict[str, float] = field(default_factory=dict)
    log: list[dict[str, object]] = field(default_factory=list)

    def send(self, request: Request) -> Response:
        timeout = DEFAULT_TIMEOUT
        self.log.append({"method": request.method, "path": request.path, "timeout": timeout})
        if self.latency.get(request.path, 0.0) > timeout:
            return Response(504, "gateway timeout")
        return self.responses.get(request.path, Response(404, "not found"))
