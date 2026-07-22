"""Ronin AI OS API v1.

Additive: mounted under /api/v1 alongside the existing SaaS + dashboard
routes, which are untouched. New tables are namespaced `aios_*` so they never
collide with the legacy briefing schema. Portable types only (SQLite locally,
PostgreSQL-compatible).
"""

from .router import build_v1_router

__all__ = ["build_v1_router"]
