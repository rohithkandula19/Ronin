"""Routing and the single place domain errors become responses."""

from __future__ import annotations

from typing import Callable, Mapping, Sequence

from ..errors import NotedeckError
from .error_map import status_for, to_body
from .request import Request, Response

Handler = Callable[[Request, Mapping[str, str]], Response]


class App:
    """A tiny exact-match router with ``{placeholder}`` path segments."""

    def __init__(self) -> None:
        self._routes: list[tuple[str, tuple[str, ...], Handler]] = []

    def add(self, method: str, template: str, handler: Handler) -> None:
        self._routes.append((method.upper(), tuple(template.strip("/").split("/")), handler))

    def include(self, routes: Sequence[tuple[str, str, Handler]]) -> None:
        for method, template, handler in routes:
            self.add(method, template, handler)

    def handle(self, request: Request) -> Response:
        segments = tuple(request.path.strip("/").split("/"))
        for method, template, handler in self._routes:
            if method != request.method.upper() or len(template) != len(segments):
                continue
            params = _match(template, segments)
            if params is None:
                continue
            try:
                return handler(request, params)
            except NotedeckError as error:
                return Response(status=status_for(error), body=to_body(error))
        return Response(
            status=404,
            body={"error": "no_route", "message": f"{request.method} {request.path}"},
        )


def _match(template: tuple[str, ...], segments: tuple[str, ...]) -> dict[str, str] | None:
    params: dict[str, str] = {}
    for pattern, value in zip(template, segments):
        if pattern.startswith("{") and pattern.endswith("}"):
            params[pattern[1:-1]] = value
        elif pattern != value:
            return None
    return params
