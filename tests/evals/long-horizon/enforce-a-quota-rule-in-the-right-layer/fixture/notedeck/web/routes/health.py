"""Liveness route."""

from __future__ import annotations

from typing import Mapping, Sequence

from ... import __version__
from ..request import Request, Response


def health_routes() -> Sequence[tuple[str, str, object]]:
    def show(request: Request, params: Mapping[str, str]) -> Response:
        return Response(status=200, body={"status": "ok", "version": __version__})

    return (("GET", "health", show),)
