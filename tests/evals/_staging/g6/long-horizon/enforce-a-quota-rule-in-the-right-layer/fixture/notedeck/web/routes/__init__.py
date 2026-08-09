"""Route tables. A route reads the request, calls one service, shapes a response."""

from __future__ import annotations

from .health import health_routes
from .notes import note_routes
from .users import user_routes

__all__ = ["health_routes", "note_routes", "user_routes"]
