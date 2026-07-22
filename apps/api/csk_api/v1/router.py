"""API v1 routers: auth, worlds, models/adapters, memory (vault), audit.

Every write records an audit event. Authorization is least-privilege: a user
only ever sees their own workspaces, world entries, and memory. Memory recall
goes through the Vault, so cross-industry isolation is enforced by the same
tested code the CLI would use — not re-implemented here.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import session_scope as _session_scope_dep

# The db module exposes session_scope as a FastAPI dependency generator; wrap it
# once as a real context manager for the imperative call sites in this module.
session_scope = contextlib.contextmanager(_session_scope_dep)
from ronin_industry_sdk import ActivationError
from ronin_vault import IsolationError, MemoryItem, MemoryScope, Sensitivity
from . import context as ctx
from .models import (
    AiosAuditEvent,
    AiosSession,
    AiosUser,
    AiosWorkspace,
    AiosWorldEntry,
)
from .security import hash_password, hash_token, new_session_token, verify_password


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit(db: Session, *, user_id: int | None, action: str, world: str = "", detail: str = "") -> None:
    db.add(AiosAuditEvent(user_id=user_id, action=action, world=world, detail=detail))


# --------------------------------------------------------------- schemas

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = ""


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    token: str
    user_id: int
    email: str


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = "personal"


class WorkspaceOut(BaseModel):
    id: int
    name: str
    kind: str


class WorldEntryIn(BaseModel):
    workspace_id: int
    world: str
    role: str
    country: str
    language: str
    privacy_mode: str = "standard"


class MemoryIn(BaseModel):
    text: str = Field(min_length=1)
    scope: MemoryScope = MemoryScope.user
    world: str = ""
    sensitivity: Sensitivity = Sensitivity.normal
    source: str = "api"


class RecallIn(BaseModel):
    query: str
    world: str
    workspace_kind: str = "personal"


# ------------------------------------------------------------- auth dep

def _current_user(
    authorization: str = Header(default=""),
) -> AiosUser:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    raw = authorization.split(" ", 1)[1].strip()
    token_hash = hash_token(raw)
    with session_scope() as db:
        sess = db.execute(
            select(AiosSession).where(
                AiosSession.token_hash == token_hash, AiosSession.revoked.is_(False)
            )
        ).scalar_one_or_none()
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid or revoked token")
        user = db.get(AiosUser, sess.user_id)
        sess.last_used_at = datetime.now(timezone.utc)
        db.expunge(user)
        return user


def build_v1_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    # ---------------------------------------------------------- auth
    auth = APIRouter(prefix="/auth", tags=["auth"])

    @auth.post("/register", response_model=TokenOut, status_code=201)
    def register(body: RegisterIn) -> TokenOut:
        with session_scope() as db:
            exists = db.execute(
                select(AiosUser).where(AiosUser.email == body.email)
            ).scalar_one_or_none()
            if exists:
                raise HTTPException(status_code=409, detail="email already registered")
            user = AiosUser(
                email=body.email,
                password_hash=hash_password(body.password),
                display_name=body.display_name,
            )
            db.add(user)
            db.flush()
            raw, token_hash = new_session_token()
            db.add(AiosSession(user_id=user.id, token_hash=token_hash))
            _audit(db, user_id=user.id, action="auth.register")
            return TokenOut(token=raw, user_id=user.id, email=user.email)

    @auth.post("/login", response_model=TokenOut)
    def login(body: LoginIn) -> TokenOut:
        with session_scope() as db:
            user = db.execute(
                select(AiosUser).where(AiosUser.email == body.email)
            ).scalar_one_or_none()
            if user is None or not verify_password(body.password, user.password_hash):
                # identical message for both cases — no account enumeration
                raise HTTPException(status_code=401, detail="invalid credentials")
            raw, token_hash = new_session_token()
            db.add(AiosSession(user_id=user.id, token_hash=token_hash))
            _audit(db, user_id=user.id, action="auth.login")
            return TokenOut(token=raw, user_id=user.id, email=user.email)

    @auth.post("/logout")
    def logout(authorization: str = Header(default="")) -> dict:
        raw = authorization.split(" ", 1)[1].strip() if " " in authorization else ""
        with session_scope() as db:
            sess = db.execute(
                select(AiosSession).where(AiosSession.token_hash == hash_token(raw))
            ).scalar_one_or_none()
            if sess:
                sess.revoked = True
                _audit(db, user_id=sess.user_id, action="auth.logout")
        return {"ok": True}

    @auth.get("/me")
    def me(user: AiosUser = Depends(_current_user)) -> dict:
        return {"id": user.id, "email": user.email, "display_name": user.display_name}

    router.include_router(auth)

    # -------------------------------------------------------- worlds
    worlds = APIRouter(prefix="/worlds", tags=["worlds"])

    def _world_dict(m) -> dict:
        return {
            "id": m.id,
            "name": m.name,
            "description": m.description,
            "status": m.status.value,
            "risk_level": m.risk_level.value,
            "roles": list(m.supported_roles),
            "countries": list(m.supported_countries),
            "languages": list(m.supported_languages),
            "default_adapter": m.default_adapter,
            "allowed_capabilities": list(m.allowed_model_capabilities),
            "allowed_tools": list(m.allowed_tools),
            "blocked_capabilities": list(m.blocked_capabilities),
        }

    @worlds.get("")
    def list_worlds(include_disabled: bool = True) -> dict:
        reg = ctx.pack_registry()
        return {
            "worlds": [_world_dict(m) for m in reg.list_packs(include_disabled=include_disabled)],
            "discovery_errors": reg.discovery_errors,
        }

    @worlds.get("/{world_id}")
    def get_world(world_id: str) -> dict:
        m = ctx.pack_registry().get(world_id)
        if m is None:
            raise HTTPException(status_code=404, detail=f"unknown world {world_id!r}")
        return _world_dict(m)

    @worlds.post("/enter")
    def enter_world(body: WorldEntryIn, user: AiosUser = Depends(_current_user)) -> dict:
        reg = ctx.pack_registry()
        suites = ctx.suite_registry()
        try:
            manifest = reg.activate(
                body.world,
                role=body.role,
                country=body.country,
                language=body.language,
                known_tools=ctx.pack_registry().get(body.world).allowed_tools if reg.get(body.world) else [],
                known_eval_suites=suites.known_ids(),
                known_adapters=[a.id for a in ctx.adapter_registry().list_adapters()],
            )
        except ActivationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with session_scope() as db:
            ws = db.get(AiosWorkspace, body.workspace_id)
            if ws is None or ws.owner_id != user.id:
                raise HTTPException(status_code=404, detail="workspace not found")
            existing = db.execute(
                select(AiosWorldEntry).where(
                    AiosWorldEntry.workspace_id == body.workspace_id,
                    AiosWorldEntry.world == body.world,
                )
            ).scalar_one_or_none()
            if existing is None:
                db.add(AiosWorldEntry(
                    workspace_id=body.workspace_id, world=body.world, role=body.role,
                    country=body.country, language=body.language, privacy_mode=body.privacy_mode,
                ))
            _audit(db, user_id=user.id, action="world.enter", world=body.world,
                   detail=f"role={body.role} country={body.country}")
        return {"ok": True, "world": _world_dict(manifest)}

    router.include_router(worlds)

    # --------------------------------------------------- workspaces
    ws_router = APIRouter(prefix="/workspaces", tags=["workspaces"])

    @ws_router.post("", response_model=WorkspaceOut, status_code=201)
    def create_workspace(body: WorkspaceIn, user: AiosUser = Depends(_current_user)) -> WorkspaceOut:
        if body.kind not in ("personal", "organization"):
            raise HTTPException(status_code=422, detail="kind must be personal or organization")
        with session_scope() as db:
            ws = AiosWorkspace(owner_id=user.id, name=body.name, kind=body.kind)
            db.add(ws)
            db.flush()
            _audit(db, user_id=user.id, action="workspace.create", detail=body.name)
            return WorkspaceOut(id=ws.id, name=ws.name, kind=ws.kind)

    @ws_router.get("")
    def list_workspaces(user: AiosUser = Depends(_current_user)) -> dict:
        with session_scope() as db:
            rows = db.execute(
                select(AiosWorkspace).where(AiosWorkspace.owner_id == user.id)
            ).scalars().all()
            return {"workspaces": [
                {"id": w.id, "name": w.name, "kind": w.kind} for w in rows
            ]}

    router.include_router(ws_router)

    # --------------------------------------------------- models/adapters
    models = APIRouter(tags=["models"])

    @models.get("/models")
    def list_models() -> dict:
        return {"models": [m.model_dump(mode="json") for m in ctx.model_registry().list_models()]}

    @models.get("/adapters")
    def list_adapters(industry: str | None = None) -> dict:
        adapters = ctx.adapter_registry().list_adapters(industry=industry)
        return {"adapters": [a.model_dump(mode="json") for a in adapters]}

    router.include_router(models)

    # --------------------------------------------------------- memory
    mem = APIRouter(prefix="/memory", tags=["memory"])

    @mem.post("", status_code=201)
    def add_memory(body: MemoryIn, user: AiosUser = Depends(_current_user)) -> dict:
        try:
            item = ctx.vault().add(MemoryItem(
                scope=body.scope, owner=str(user.id), text=body.text, world=body.world,
                sensitivity=body.sensitivity, source=body.source, created=_now(),
            ))
        except IsolationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with session_scope() as db:
            _audit(db, user_id=user.id, action="memory.add", world=body.world)
        return item.model_dump(mode="json")

    @mem.get("")
    def list_memory(user: AiosUser = Depends(_current_user)) -> dict:
        items = ctx.vault().list_items(str(user.id))
        return {"items": [i.model_dump(mode="json") for i in items]}

    @mem.post("/recall")
    def recall_memory(body: RecallIn, user: AiosUser = Depends(_current_user)) -> dict:
        hits = ctx.vault().recall(
            body.query, owner=str(user.id), world=body.world,
            workspace_kind=body.workspace_kind, when=_now(),
        )
        return {"hits": [i.model_dump(mode="json") for i in hits]}

    @mem.delete("/{item_id}")
    def delete_memory(item_id: str, user: AiosUser = Depends(_current_user)) -> dict:
        item = ctx.vault().get(item_id)
        if item is None or item.owner != str(user.id):
            raise HTTPException(status_code=404, detail="memory item not found")
        ctx.vault().delete(item_id)
        with session_scope() as db:
            _audit(db, user_id=user.id, action="memory.delete")
        return {"ok": True}

    router.include_router(mem)

    # ---------------------------------------------------------- audit
    audit_router = APIRouter(prefix="/audit", tags=["audit"])

    @audit_router.get("")
    def list_audit(user: AiosUser = Depends(_current_user), limit: int = 50) -> dict:
        limit = max(1, min(limit, 200))
        with session_scope() as db:
            rows = db.execute(
                select(AiosAuditEvent)
                .where(AiosAuditEvent.user_id == user.id)
                .order_by(AiosAuditEvent.id.desc())
                .limit(limit)
            ).scalars().all()
            return {"events": [
                {"at": e.at.isoformat(), "action": e.action, "world": e.world, "detail": e.detail}
                for e in rows
            ]}

    router.include_router(audit_router)

    return router
