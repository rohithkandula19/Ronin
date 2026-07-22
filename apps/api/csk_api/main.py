"""FastAPI app — exposes the hosted SaaS API."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from .billing import (
    BillingConfig,
    apply_webhook_event,
    create_checkout_session,
    create_portal_session,
    verify_stripe_signature,
)
from .config import generate_api_token, get_settings
from .db import ALLOWED_SERVICES, Plan, init_db, session_scope
from .oauth import build_authorize_url, exchange_code
from .services import (
    create_user,
    list_briefings,
    list_connections,
    run_agent_message,
    run_briefing_for_user,
    store_connection,
    upsert_schedule,
    user_for_token,
)


# ---------- request / response models ----------

class SignupIn(BaseModel):
    email: EmailStr


class SignupOut(BaseModel):
    id: int
    email: str
    api_token: str  # shown once at signup; never returned again


class ConnectionIn(BaseModel):
    service: str = Field(..., description=f"One of: {sorted(ALLOWED_SERVICES)}")
    secret: str


class ConnectionOut(BaseModel):
    service: str


class BriefingOut(BaseModel):
    id: int
    markdown: str
    mrr_cents: int
    active_subs: int
    failed_charges_7d: int
    created_at: datetime


class ScheduleIn(BaseModel):
    cron: str | None = None
    slack_channel: str | None = None
    enabled: bool = True


class AgentMessageIn(BaseModel):
    message: str = Field(..., description="The inbound message to run through the agent.")


class AgentMessageOut(BaseModel):
    ok: bool
    reply: str
    demo_mode: bool
    error: str | None = None


class ScheduleOut(BaseModel):
    cron: str
    slack_channel: str
    enabled: bool
    last_run_at: datetime | None


# ---------- dependencies ----------

def db_dep() -> Iterator[Session]:
    yield from session_scope()


def current_user(
    session: Session = Depends(db_dep),
    authorization: str | None = Header(default=None),
):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    token = authorization.split(None, 1)[1].strip()
    user = user_for_token(session, token)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid api token")
    return user


# ---------- app ----------

def make_app() -> FastAPI:
    settings = get_settings()
    # Import v1 models before init_db so their aios_* tables are created too.
    from . import v1 as _v1  # noqa: F401
    from .v1.models import AiosUser  # noqa: F401  (registers all aios_* tables)
    init_db()
    app = FastAPI(
        title="ronin — hosted briefings API",
        version="0.0.1",
        description="Per-user storage + scheduled execution of csk briefings.",
        debug=settings.debug,
    )

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "version": "0.0.1"}

    # ---------- web dashboard (ronin ui) ----------
    #
    # A single self-contained HTML page (inline CSS, vanilla JS, no external
    # resource URLs, fully offline) plus a few READ-ONLY JSON endpoints it calls.
    # The endpoints surface REAL stored data — orchestrated runs and chat
    # sessions, a run's planner -> sub-agent tree with faithfulness scores, the
    # durable memory store, and crystallized skills — read from the same .ronin
    # home the CLI writes to. No auth: read-only on local data, served on
    # localhost by `ronin ui`. When there is no data yet the UI shows an honest
    # empty state.

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard() -> HTMLResponse:
        from .dashboard_html import DASHBOARD_HTML
        return HTMLResponse(content=DASHBOARD_HTML)

    @app.get("/ui/runs")
    def ui_runs(limit: int = 50) -> list[dict]:
        from .dashboard import recent_runs
        return recent_runs(limit=max(1, min(200, limit)))

    @app.get("/ui/runs/{run_id}")
    def ui_run_detail(run_id: str) -> dict:
        from .dashboard import run_detail
        detail = run_detail(run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="run not found")
        return detail

    @app.get("/ui/memory")
    def ui_memory(limit: int = 200) -> list[dict]:
        from .dashboard import memory_entries
        return memory_entries(limit=max(1, min(500, limit)))

    @app.get("/ui/skills")
    def ui_skills() -> list[dict]:
        from .dashboard import skill_entries
        return skill_entries(".")

    @app.post("/signup", response_model=SignupOut, status_code=201)
    def signup(body: SignupIn, session: Session = Depends(db_dep)) -> SignupOut:
        token = generate_api_token(settings.api_token_bytes)
        try:
            user = create_user(session, str(body.email), token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return SignupOut(id=user.id, email=user.email, api_token=token)

    @app.post("/connections", response_model=ConnectionOut, status_code=201)
    def add_connection(
        body: ConnectionIn,
        session: Session = Depends(db_dep),
        user=Depends(current_user),
    ) -> ConnectionOut:
        try:
            store_connection(session, user, body.service, body.secret)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return ConnectionOut(service=body.service)

    @app.get("/connections", response_model=list[ConnectionOut])
    def get_connections(
        session: Session = Depends(db_dep),
        user=Depends(current_user),
    ) -> list[ConnectionOut]:
        return [ConnectionOut(service=name) for name in list_connections(session, user)]

    @app.post("/briefings", response_model=BriefingOut, status_code=201)
    def run_briefing(
        session: Session = Depends(db_dep),
        user=Depends(current_user),
    ) -> BriefingOut:
        run = run_briefing_for_user(session, user)
        return BriefingOut(
            id=run.id,
            markdown=run.markdown,
            mrr_cents=run.mrr_cents,
            active_subs=run.active_subs,
            failed_charges_7d=run.failed_charges_7d,
            created_at=run.created_at,
        )

    @app.get("/briefings", response_model=list[BriefingOut])
    def get_briefings(
        limit: int = 25,
        session: Session = Depends(db_dep),
        user=Depends(current_user),
    ) -> list[BriefingOut]:
        runs = list_briefings(session, user, limit=max(1, min(100, limit)))
        return [
            BriefingOut(
                id=r.id,
                markdown=r.markdown,
                mrr_cents=r.mrr_cents,
                active_subs=r.active_subs,
                failed_charges_7d=r.failed_charges_7d,
                created_at=r.created_at,
            )
            for r in runs
        ]

    @app.post("/briefings/schedule", response_model=ScheduleOut)
    def set_schedule(
        body: ScheduleIn,
        session: Session = Depends(db_dep),
        user=Depends(current_user),
    ) -> ScheduleOut:
        schedule = upsert_schedule(
            session,
            user,
            cron=body.cron,
            slack_channel=body.slack_channel,
            enabled=body.enabled,
        )
        return ScheduleOut(
            cron=schedule.cron,
            slack_channel=schedule.slack_channel,
            enabled=bool(schedule.enabled),
            last_run_at=schedule.last_run_at,
        )

    # ---------- agent webhook gateway ----------
    #
    # A generic inbound-message endpoint: POST a message and the authenticated
    # user's agent runs it and returns the reply. This is a plain HTTP gateway,
    # no Telegram/Slack account or live integration is required. A chat platform
    # can forward messages here with a thin adapter (translate its webhook shape
    # to {"message": "..."} plus the user's Bearer token), but that adapter is
    # OPTIONAL and not shipped here. With no provider key stored, the agent
    # answers from ronin's offline demo brain (no network egress).
    @app.post("/webhooks/agent", response_model=AgentMessageOut)
    def agent_gateway(
        body: AgentMessageIn,
        session: Session = Depends(db_dep),
        user=Depends(current_user),
    ) -> AgentMessageOut:
        result = run_agent_message(session, user, body.message)
        return AgentMessageOut(
            ok=bool(result["ok"]),
            reply=result.get("reply", ""),
            demo_mode=bool(result.get("demo_mode", False)),
            error=result.get("error"),
        )

    # ---------- OAuth ----------

    @app.get("/oauth/{provider}/start")
    def oauth_start(provider: str, user=Depends(current_user)) -> RedirectResponse:
        try:
            url = build_authorize_url(provider, user.id)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return RedirectResponse(url, status_code=302)

    @app.get("/oauth/{provider}/callback")
    def oauth_callback(
        provider: str,
        code: str,
        state: str,
        session: Session = Depends(db_dep),
    ) -> dict[str, Any]:
        try:
            conn = exchange_code(session, provider, state, code)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "service": conn.service}

    # ---------- Billing ----------

    @app.post("/billing/checkout")
    def billing_checkout(
        plan: str,
        user=Depends(current_user),
    ) -> dict[str, str]:
        try:
            plan_enum = Plan(plan.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unknown plan {plan!r}")
        try:
            url = create_checkout_session(user, plan_enum)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"url": url}

    @app.post("/billing/portal")
    def billing_portal(
        return_url: str | None = None,
        user=Depends(current_user),
    ) -> dict[str, str]:
        """Open the Stripe Customer Portal — self-serve plan changes + cancel."""
        try:
            url = create_portal_session(user, return_url=return_url)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"url": url}

    @app.post("/webhooks/stripe")
    async def stripe_webhook(
        request: Request,
        session: Session = Depends(db_dep),
    ) -> dict[str, Any]:
        payload = await request.body()
        signature = request.headers.get("Stripe-Signature", "")
        cfg = BillingConfig.from_env()
        if cfg.webhook_secret and not verify_stripe_signature(payload, signature, cfg.webhook_secret):
            raise HTTPException(status_code=400, detail="invalid signature")
        try:
            event = json.loads(payload.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"bad payload: {exc}")
        message = apply_webhook_event(session, event)
        return {"ok": True, "message": message}

    # ---------- Ronin AI OS API v1 (additive, mounted under /api/v1) ----------
    from .v1 import build_v1_router

    app.include_router(build_v1_router())

    return app


app = make_app()
