"""API v1 ORM models. Namespaced aios_* to coexist with the legacy schema.

Portable column types only (String/Integer/Boolean/Text/DateTime) so the same
schema runs on SQLite locally and PostgreSQL in production. Created via the
existing Base.metadata.create_all — Alembic migrations are the documented
production step (see docs/architecture).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AiosUser(Base):
    __tablename__ = "aios_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))  # pbkdf2, never plaintext
    display_name: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    sessions: Mapped[list["AiosSession"]] = relationship(back_populates="user")
    workspaces: Mapped[list["AiosWorkspace"]] = relationship(back_populates="owner")


class AiosSession(Base):
    __tablename__ = "aios_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("aios_users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    user_agent: Mapped[str] = mapped_column(String(255), default="")

    user: Mapped[AiosUser] = relationship(back_populates="sessions")


class AiosWorkspace(Base):
    __tablename__ = "aios_workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("aios_users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="personal")  # personal|organization
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    owner: Mapped[AiosUser] = relationship(back_populates="workspaces")


class AiosWorldEntry(Base):
    """A user's onboarding into a world (role/country/language choices)."""

    __tablename__ = "aios_world_entries"
    __table_args__ = (UniqueConstraint("workspace_id", "world", name="uq_ws_world"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("aios_workspaces.id"), index=True)
    world: Mapped[str] = mapped_column(String(60))
    role: Mapped[str] = mapped_column(String(60))
    country: Mapped[str] = mapped_column(String(2))
    language: Mapped[str] = mapped_column(String(10))
    privacy_mode: Mapped[str] = mapped_column(String(20), default="standard")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AiosAuditEvent(Base):
    """Append-only audit trail. Rows are never updated in place."""

    __tablename__ = "aios_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    world: Mapped[str] = mapped_column(String(60), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
