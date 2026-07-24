"""SQLAlchemy models + session management.

For dev/tests we default to a local sqlite file (``csk_saas.db``). Production
overrides ``DATABASE_URL`` to a real Postgres URI. The schema is small enough
to manage with ``Base.metadata.create_all`` for now; migrate to Alembic when
the first prod user signs up.

Tables:
- ``users``: signup record + hashed API token
- ``service_connections``: encrypted credential per (user, service)
- ``briefing_runs``: history of generated briefings (Markdown stored inline)
- ``schedules``: cron triggers per user
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterator

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    """Mapped Base for the SaaS schema.

    ``__allow_unmapped__ = True`` keeps the SQLAlchemy 2.0 declarative validator
    happy with the type hints we use on ``Column(...)`` attributes (which are
    informational, not Mapped[]). Migrating to full ``Mapped[T]`` typings is
    a future refactor.
    """

    __allow_unmapped__ = True


class User(Base):
    __tablename__ = "users"

    id: int = Column(Integer, primary_key=True)  # type: ignore[assignment]
    email: str = Column(String(320), unique=True, nullable=False)  # type: ignore[assignment]
    api_token_hash: str = Column(String(128), nullable=False)  # type: ignore[assignment]
    created_at: datetime = Column(DateTime, default=datetime.utcnow, nullable=False)  # type: ignore[assignment]

    connections: list["ServiceConnection"] = relationship(
        "ServiceConnection", back_populates="user", cascade="all, delete-orphan"
    )
    briefings: list["BriefingRun"] = relationship(
        "BriefingRun", back_populates="user", cascade="all, delete-orphan"
    )
    schedule: "Schedule" = relationship(
        "Schedule", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


# Services the user can connect. New ones get added here.
ALLOWED_SERVICES = {"anthropic", "openai", "stripe", "linear", "slack_bot", "notion", "database_url"}


class ServiceConnection(Base):
    __tablename__ = "service_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "service", name="uq_user_service"),
        Index("ix_user_service", "user_id", "service"),
    )

    id: int = Column(Integer, primary_key=True)  # type: ignore[assignment]
    user_id: int = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)  # type: ignore[assignment]
    service: str = Column(String(64), nullable=False)  # type: ignore[assignment]
    secret_ciphertext: str = Column(Text, nullable=False)  # type: ignore[assignment]
    extra: str = Column(Text, default="")  # type: ignore[assignment]
    created_at: datetime = Column(DateTime, default=datetime.utcnow, nullable=False)  # type: ignore[assignment]

    user: User = relationship("User", back_populates="connections")


class BriefingRun(Base):
    __tablename__ = "briefing_runs"
    __table_args__ = (
        Index("ix_user_created", "user_id", "created_at"),
    )

    id: int = Column(Integer, primary_key=True)  # type: ignore[assignment]
    user_id: int = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)  # type: ignore[assignment]
    markdown: str = Column(Text, nullable=False)  # type: ignore[assignment]
    mrr_cents: int = Column(Integer, default=0, nullable=False)  # type: ignore[assignment]
    active_subs: int = Column(Integer, default=0, nullable=False)  # type: ignore[assignment]
    failed_charges_7d: int = Column(Integer, default=0, nullable=False)  # type: ignore[assignment]
    created_at: datetime = Column(DateTime, default=datetime.utcnow, nullable=False)  # type: ignore[assignment]

    user: User = relationship("User", back_populates="briefings")


class Schedule(Base):
    __tablename__ = "schedules"

    id: int = Column(Integer, primary_key=True)  # type: ignore[assignment]
    user_id: int = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)  # type: ignore[assignment]
    cron: str = Column(String(64), default="0 9 * * 1")  # 09:00 every Monday  # type: ignore[assignment]
    slack_channel: str = Column(String(128), default="")  # type: ignore[assignment]
    enabled: bool = Column(Integer, default=1, nullable=False)  # type: ignore[assignment]
    last_run_at: datetime | None = Column(DateTime, nullable=True)  # type: ignore[assignment]

    user: User = relationship("User", back_populates="schedule")


# ---------- engine / session ----------

_engine: Any = None
_SessionLocal: Any = None


def _get_engine() -> Any:
    global _engine, _SessionLocal
    if _engine is None:
        url = get_settings().database_url
        kwargs: dict[str, Any] = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def init_db() -> None:
    """Create all tables. Safe to call repeatedly."""
    Base.metadata.create_all(_get_engine())


def reset_db_for_tests(url: str) -> None:
    """Wipe + rebuild the schema against ``url``. Used only by the test harness."""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None
    # Settings caches the URL; reset both.
    from .config import reset_settings_for_tests

    reset_settings_for_tests()
    import os

    os.environ["DATABASE_URL"] = url
    _get_engine()
    Base.metadata.drop_all(_get_engine())
    Base.metadata.create_all(_get_engine())


def session_scope() -> Iterator[Session]:
    """FastAPI dependency: yields a session, commits on success, rolls back on error."""
    _get_engine()
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
