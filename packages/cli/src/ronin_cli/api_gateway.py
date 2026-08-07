"""Scoped local HTTP gateway for project-bound Ronin API keys.

The gateway is intentionally read-only. It is suitable for CI status checks,
editor integrations, and remote control-plane observability, but cannot grant
approval, modify a checkout, invoke a provider, or access provider secrets.
"""
from __future__ import annotations

from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException

from .api_keys import ApiKeyError, ApiKeyLease, ApiKeyStore
from .mission_store import MissionStore


def make_app(root: Path | str) -> FastAPI:
    """Build a project-bound, authenticated read-only control-plane API."""
    project_root = Path(root).resolve()
    key_store = ApiKeyStore(project_root)
    mission_store = MissionStore(project_root)
    app = FastAPI(
        title="Ronin Project Gateway",
        version="v1",
        description="Scoped, local, read-only project control-plane access.",
    )

    def require(scope: str) -> Callable[..., Generator[ApiKeyLease, None, None]]:
        def authenticated_request(
            authorization: str | None = Header(None),
        ) -> Generator[ApiKeyLease, None, None]:
            token = _bearer_token(authorization)
            try:
                lease = key_store.reserve(token, scope=scope)
            except ApiKeyError as exc:
                raise _http_error(exc) from exc
            try:
                yield lease
            finally:
                # Status endpoints have no provider usage. Settling is still
                # required to release the key's concurrency reservation.
                try:
                    key_store.settle(lease)
                except ApiKeyError:
                    pass

        return authenticated_request

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "ronin-project-gateway", "mode": "read_only"}

    @app.get("/v1/identity")
    def identity(lease: ApiKeyLease = Depends(require("read"))) -> dict[str, Any]:
        return {"authenticated": True, "key_id": lease.key_id, "project_bound": True}

    @app.get("/v1/missions")
    def list_missions(_: ApiKeyLease = Depends(require("mission:read"))) -> dict[str, Any]:
        return {"missions": [_mission_summary(mission) for mission in mission_store.list(limit=100)]}

    @app.get("/v1/missions/{mission_id}")
    def show_mission(mission_id: str, _: ApiKeyLease = Depends(require("mission:read"))) -> dict[str, Any]:
        try:
            mission = mission_store.load(mission_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="mission was not found") from exc
        return _mission_summary(mission)

    return app


def _bearer_token(value: str | None) -> str:
    scheme, _, token = (value or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Bearer Ronin API key required")
    return token.strip()


def _http_error(error: ApiKeyError) -> HTTPException:
    message = str(error)
    if "scope" in message:
        return HTTPException(status_code=403, detail="Ronin API key scope denied")
    if "limit" in message:
        return HTTPException(status_code=429, detail="Ronin API key policy limit reached")
    return HTTPException(status_code=401, detail="Ronin API key rejected")


def _mission_summary(mission: Any) -> dict[str, Any]:
    """Return operational metadata without task text, artifacts, or paths."""
    return {
        "id": mission.id,
        "title": mission.spec.title,
        "stage": mission.stage.value,
        "updated": mission.updated,
        "event_count": mission.event_count,
        "candidate_attached": bool(mission.candidate_workspace_id),
        "usage": {
            "tokens": mission.usage.tokens,
            "cost_usd": mission.usage.cost_usd,
            "tool_calls": mission.usage.tool_calls,
        },
    }
