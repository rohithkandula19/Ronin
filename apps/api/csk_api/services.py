"""Business logic — orchestrates DB rows and the CLI's briefing engine.

The CLI lives in ``ro_claude_kit_cli``; we reuse its data aggregator and Markdown
renderer directly. The SaaS just provides per-user persistence + a way to run
the briefing on a cron without anyone opening a terminal.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ro_claude_kit_cli.briefing import compute_briefing_data, render_briefing_md
from ro_claude_kit_cli.briefing_history import (
    BriefingDelta,
    BriefingSnapshot,
    format_delta_line,
)
from ro_claude_kit_cli.config import CSKConfig
from ro_claude_kit_cli.tools import build_tools

from .crypto import decrypt, encrypt, hash_api_token
from .db import ALLOWED_SERVICES, BriefingRun, Schedule, ServiceConnection, User


# ---------- user lifecycle ----------

def create_user(session: Session, email: str, raw_api_token: str) -> User:
    if not email or "@" not in email:
        raise ValueError("valid email is required")
    existing = session.query(User).filter_by(email=email).first()
    if existing is not None:
        raise ValueError(f"user with email {email!r} already exists")
    user = User(email=email, api_token_hash=hash_api_token(raw_api_token))
    session.add(user)
    session.flush()
    return user


def user_for_token(session: Session, raw_api_token: str) -> User | None:
    if not raw_api_token:
        return None
    return session.query(User).filter_by(api_token_hash=hash_api_token(raw_api_token)).first()


# ---------- connections ----------

def store_connection(session: Session, user: User, service: str, secret: str) -> ServiceConnection:
    if service not in ALLOWED_SERVICES:
        raise ValueError(f"unknown service {service!r} (allowed: {sorted(ALLOWED_SERVICES)})")
    if not secret:
        raise ValueError("secret is required")

    existing = session.query(ServiceConnection).filter_by(user_id=user.id, service=service).first()
    if existing is not None:
        existing.secret_ciphertext = encrypt(secret)
        return existing

    conn = ServiceConnection(user_id=user.id, service=service, secret_ciphertext=encrypt(secret))
    session.add(conn)
    session.flush()
    return conn


def list_connections(session: Session, user: User) -> list[str]:
    """Returns the configured service names only — never the secrets."""
    return [
        c.service
        for c in session.query(ServiceConnection).filter_by(user_id=user.id).order_by(ServiceConnection.service).all()
    ]


def config_for_user(session: Session, user: User) -> CSKConfig:
    """Decrypt the user's stored credentials into a CSKConfig the CLI can use."""
    conns = {
        c.service: decrypt(c.secret_ciphertext)
        for c in session.query(ServiceConnection).filter_by(user_id=user.id).all()
    }
    return CSKConfig(
        anthropic_api_key=conns.get("anthropic"),
        openai_api_key=conns.get("openai"),
        stripe_api_key=conns.get("stripe"),
        linear_api_key=conns.get("linear"),
        slack_bot_token=conns.get("slack_bot"),
        notion_token=conns.get("notion"),
        database_url=conns.get("database_url"),
    )


# ---------- briefings ----------

def run_briefing_for_user(session: Session, user: User) -> BriefingRun:
    """Compute + persist a fresh briefing. Returns the saved row."""
    config = config_for_user(session, user)
    tools = build_tools(config)
    data = compute_briefing_data(tools)
    md = render_briefing_md(data)

    # Inline week-over-week delta from the most recent prior run, if any.
    prior = (
        session.query(BriefingRun)
        .filter_by(user_id=user.id)
        .order_by(BriefingRun.created_at.desc())
        .first()
    )
    if prior is not None:
        prior_snapshot = BriefingSnapshot(
            date=prior.created_at.date().isoformat(),
            mrr_cents=prior.mrr_cents,
            active_subs=prior.active_subs,
            failed_charges_7d=prior.failed_charges_7d,
        )
        current_snapshot = BriefingSnapshot.from_briefing(data)
        delta = BriefingDelta.compute(current_snapshot, prior_snapshot)
        md = md.rstrip() + "\n\n" + format_delta_line(delta, prior_snapshot.date) + "\n"

    run = BriefingRun(
        user_id=user.id,
        markdown=md,
        mrr_cents=data.mrr_cents,
        active_subs=len(data.active_subs),
        failed_charges_7d=len(data.failed_charges_7d),
    )
    session.add(run)
    session.flush()
    return run


def list_briefings(session: Session, user: User, limit: int = 25) -> list[BriefingRun]:
    return (
        session.query(BriefingRun)
        .filter_by(user_id=user.id)
        .order_by(BriefingRun.created_at.desc())
        .limit(limit)
        .all()
    )


# ---------- schedule ----------

def upsert_schedule(
    session: Session,
    user: User,
    *,
    cron: str | None = None,
    slack_channel: str | None = None,
    enabled: bool = True,
) -> Schedule:
    schedule = session.query(Schedule).filter_by(user_id=user.id).first()
    if schedule is None:
        schedule = Schedule(user_id=user.id)
        session.add(schedule)
    if cron is not None:
        schedule.cron = cron
    if slack_channel is not None:
        schedule.slack_channel = slack_channel
    schedule.enabled = 1 if enabled else 0
    session.flush()
    return schedule


def due_users(session: Session, now: datetime | None = None) -> list[User]:
    """Users whose schedules say they're due. The cron worker calls this each tick.

    For the MVP we keep the cron check trivial: re-run if the last run was over
    6 days ago and the schedule is enabled. Production swaps this for a real
    croniter check on ``schedule.cron``.
    """
    from datetime import timedelta

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    threshold = now - timedelta(days=6, hours=23)
    users: list[User] = []
    for schedule in session.query(Schedule).filter_by(enabled=1).all():
        if schedule.last_run_at is None or schedule.last_run_at < threshold:
            users.append(schedule.user)
    return users


def mark_schedule_ran(session: Session, user: User) -> None:
    schedule = session.query(Schedule).filter_by(user_id=user.id).first()
    if schedule is not None:
        schedule.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.flush()
