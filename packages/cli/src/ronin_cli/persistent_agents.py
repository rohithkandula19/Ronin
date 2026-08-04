"""Durable local supervision for Ronin's long-lived specialist team.

This is intentionally a control-plane store, not a hidden daemon. It preserves
role identity, in-flight assignment, health, experience, and compact context
across CLI processes so a worker process can be restarted without inventing
state. Provider calls and code execution remain owned by the existing governed
orchestration and candidate-workspace paths.
"""
from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from pydantic import Field

from .mission_models import StrictModel, now_utc


TEAM_SCHEMA_VERSION = 1
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_TEAM_ROLES = ("architect", "implementer", "reviewer", "tester", "security", "release")
AgentRole = Literal["architect", "implementer", "reviewer", "tester", "security", "release", "meta"]
AgentStatus = Literal["idle", "assigned", "running", "completed", "crashed"]
MemorySourceType = Literal[
    "mission", "issue", "pull_request", "commit", "human", "skill", "adr", "workaround", "compaction",
]
ContextItemKind = Literal["file", "test", "convention", "experience"]

_AGENT_ID = re.compile(r"^[a-z][a-z0-9-]{1,79}$")
_TOKENS = re.compile(r"[a-z0-9_]+")
_TRANSITIONS: dict[str, frozenset[str]] = {
    "idle": frozenset({"assigned"}),
    "assigned": frozenset({"running", "idle", "crashed"}),
    "running": frozenset({"completed", "crashed"}),
    "completed": frozenset({"idle"}),
    "crashed": frozenset({"assigned", "idle"}),
}


def _parse_time(value: str) -> _datetime.datetime:
    parsed = _datetime.datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=_datetime.timezone.utc)


def _at(offset_days: int) -> str:
    return (_datetime.datetime.now(_datetime.timezone.utc) + _datetime.timedelta(days=offset_days)).isoformat(
        timespec="seconds",
    )


class PersistentAgent(StrictModel):
    """One durable role identity with its current recoverable assignment."""

    agent_id: str = Field(min_length=2, max_length=80)
    role: AgentRole
    status: AgentStatus = "idle"
    current_mission_id: str | None = Field(default=None, max_length=200)
    current_task: str = Field(default="", max_length=8_000)
    operator_summary: str = Field(default="", max_length=8_000)
    health_check_at: str = Field(default_factory=now_utc)
    restart_count: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=now_utc)
    updated_at: str = Field(default_factory=now_utc)


class AgentMemoryEntry(StrictModel):
    """Attributed role experience with expiry and access accounting."""

    memory_id: str = Field(min_length=1, max_length=80)
    agent_id: str = Field(min_length=2, max_length=80)
    content: str = Field(min_length=1, max_length=8_000)
    source_type: MemorySourceType
    source_id: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    created_at: str = Field(default_factory=now_utc)
    expires_at: str = Field(default_factory=lambda: _at(90))
    last_accessed_at: str = Field(default_factory=now_utc)
    access_count: int = Field(default=0, ge=0)


class TeamAuditEvent(StrictModel):
    """Compact hash-chain entry; details remain in the local SQLite records."""

    sequence: int = Field(ge=1)
    event_id: str = Field(min_length=64, max_length=64)
    agent_id: str = Field(min_length=2, max_length=80)
    kind: str = Field(min_length=1, max_length=100)
    from_status: AgentStatus | None = None
    to_status: AgentStatus | None = None
    mission_id: str = Field(default="", max_length=200)
    occurred_at: str = Field(min_length=1, max_length=100)
    payload_digest: str = Field(min_length=64, max_length=64)
    previous_hash: str = Field(default="", max_length=64)
    event_hash: str = Field(min_length=64, max_length=64)


class TeamAuditReport(StrictModel):
    valid: bool
    events: int
    error: str | None = None


class ContextItem(StrictModel):
    kind: ContextItemKind
    reference: str = Field(min_length=1, max_length=1_000)
    content: str = Field(default="", max_length=8_000)
    relevance: float = Field(ge=0.0, le=1.0)
    truncated: bool = False


class TeamContextPack(StrictModel):
    """Token-bounded evidence for one role; never a raw chat transcript."""

    agent_id: str = Field(min_length=2, max_length=80)
    role: AgentRole
    mission_id: str = Field(default="", max_length=200)
    task: str = Field(min_length=1, max_length=8_000)
    items: list[ContextItem] = Field(default_factory=list, max_length=32)
    estimated_tokens: int = Field(ge=0)
    max_tokens: int = Field(ge=64, le=32_000)
    truncated: bool = False
    generated_at: str = Field(default_factory=now_utc)


@dataclass(frozen=True)
class CompactionResult:
    archived: int
    summary_memory_id: str | None


class PersistentAgentStore:
    """SQLite supervisor for local role identity, memory, and recovery state."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.path = self.root / ".ronin" / "persistent-agents.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def bootstrap(self, roles: Iterable[AgentRole] = DEFAULT_TEAM_ROLES) -> tuple[PersistentAgent, ...]:
        """Ensure one stable local identity per requested role without replacing state."""
        requested = tuple(dict.fromkeys(roles))
        if not requested:
            raise ValueError("persistent team needs at least one role")
        if any(role not in {"architect", "implementer", "reviewer", "tester", "security", "release", "meta"} for role in requested):
            raise ValueError("persistent team contains an unknown role")
        with self._connect() as con:
            for role in requested:
                agent_id = f"{role}-01"
                if con.execute("SELECT 1 FROM persistent_agents WHERE agent_id = ?", (agent_id,)).fetchone():
                    continue
                now = now_utc()
                con.execute(
                    """
                    INSERT INTO persistent_agents(
                        agent_id, role, status, current_mission_id, current_task,
                        operator_summary, health_check_at, restart_count, created_at, updated_at
                    ) VALUES (?, ?, 'idle', NULL, '', '', ?, 0, ?, ?)
                    """,
                    (agent_id, role, now, now, now),
                )
                self._append_event(con, agent_id, "agent_registered", None, "idle", "", {"role": role})
        return tuple(self.get(f"{role}-01") for role in requested)

    def get(self, agent_id: str) -> PersistentAgent:
        safe_id = _safe_agent_id(agent_id)
        with self._connect() as con:
            row = con.execute("SELECT * FROM persistent_agents WHERE agent_id = ?", (safe_id,)).fetchone()
        if row is None:
            raise ValueError(f"persistent agent {safe_id!r} was not found")
        return _agent_from_row(row)

    def list(self, *, limit: int = 100) -> tuple[PersistentAgent, ...]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM persistent_agents ORDER BY role, agent_id LIMIT ?", (max(1, min(limit, 500)),),
            ).fetchall()
        return tuple(_agent_from_row(row) for row in rows)

    def assign(self, agent_id: str, mission_id: str, task: str) -> PersistentAgent:
        """Reserve an idle role for one mission; no provider or worker starts here."""
        clean_mission = mission_id.strip()
        clean_task = task.strip()
        if not clean_mission or len(clean_mission) > 200 or not clean_task or len(clean_task) > 8_000:
            raise ValueError("agent assignment requires a bounded mission id and task")
        return self._transition(
            agent_id, "assigned", kind="agent_assigned", mission_id=clean_mission,
            updates={"current_mission_id": clean_mission, "current_task": clean_task, "operator_summary": ""},
        )

    def start(self, agent_id: str) -> PersistentAgent:
        return self._transition(agent_id, "running", kind="agent_started")

    def complete(self, agent_id: str, summary: str = "") -> PersistentAgent:
        clean_summary = summary.strip()
        if len(clean_summary) > 8_000:
            raise ValueError("agent completion summary exceeds 8,000 characters")
        return self._transition(
            agent_id, "completed", kind="agent_completed", updates={"operator_summary": clean_summary},
        )

    def release(self, agent_id: str) -> PersistentAgent:
        """Return a completed or crashed role to idle after an explicit review."""
        return self._transition(
            agent_id, "idle", kind="agent_released",
            updates={"current_mission_id": None, "current_task": "", "operator_summary": ""},
        )

    def heartbeat(self, agent_id: str, *, at: str | None = None) -> PersistentAgent:
        safe_id = _safe_agent_id(agent_id)
        stamp = at or now_utc()
        _parse_time(stamp)
        with self._connect() as con:
            agent = self._require_agent(con, safe_id)
            con.execute(
                "UPDATE persistent_agents SET health_check_at = ?, updated_at = ? WHERE agent_id = ?",
                (stamp, stamp, safe_id),
            )
            self._append_event(con, safe_id, "agent_heartbeat", agent.status, agent.status, agent.current_mission_id or "", {"at": stamp})
        return self.get(safe_id)

    def supervise(
        self,
        *,
        max_age_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
        now: str | None = None,
        auto_restart: bool = True,
    ) -> tuple[PersistentAgent, ...]:
        """Mark stale in-flight roles and preserve their assignment for restart.

        This does not launch a hidden process. A worker runner calls this before
        claiming work; when ``auto_restart`` is enabled the durable state returns
        to ``assigned`` with a restart count, ready for the governed executor.
        """
        if not 1 <= max_age_seconds <= 86_400:
            raise ValueError("heartbeat age must be between 1 and 86,400 seconds")
        check_at = _parse_time(now or now_utc())
        changed: list[str] = []
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM persistent_agents WHERE status IN ('assigned', 'running')",
            ).fetchall()
            for row in rows:
                agent = _agent_from_row(row)
                age = (check_at - _parse_time(agent.health_check_at)).total_seconds()
                if age <= max_age_seconds:
                    continue
                stamp = check_at.isoformat(timespec="seconds")
                con.execute(
                    "UPDATE persistent_agents SET status = 'crashed', updated_at = ? WHERE agent_id = ?",
                    (stamp, agent.agent_id),
                )
                self._append_event(con, agent.agent_id, "agent_crashed", agent.status, "crashed", agent.current_mission_id or "", {"stale_seconds": int(age)})
                if auto_restart and agent.current_mission_id:
                    con.execute(
                        """
                        UPDATE persistent_agents
                        SET status = 'assigned', restart_count = restart_count + 1,
                            health_check_at = ?, updated_at = ?
                        WHERE agent_id = ?
                        """,
                        (stamp, stamp, agent.agent_id),
                    )
                    self._append_event(con, agent.agent_id, "agent_restart_requested", "crashed", "assigned", agent.current_mission_id, {"restart": True})
                changed.append(agent.agent_id)
        return tuple(self.get(agent_id) for agent_id in changed)

    def remember(
        self,
        agent_id: str,
        content: str,
        *,
        source_type: MemorySourceType,
        source_id: str,
        confidence: float = 0.7,
        expires_at: str | None = None,
    ) -> AgentMemoryEntry:
        """Add one source-attributed experience entry after rejecting likely secrets."""
        safe_id = _safe_agent_id(agent_id)
        clean_content = " ".join(content.split()).strip()
        clean_source = source_id.strip()
        if not clean_content or len(clean_content) > 8_000 or not clean_source or len(clean_source) > 500:
            raise ValueError("agent memory requires bounded content and source provenance")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("agent memory confidence must be between 0 and 1")
        from .memory_store import secret_labels

        if secret_labels(clean_content):
            raise ValueError("refusing to store a possible secret in agent memory")
        expiry = expires_at or _default_expiry(source_type, confidence)
        _parse_time(expiry)
        entry = AgentMemoryEntry(
            memory_id=f"memory-{uuid.uuid4().hex}", agent_id=safe_id, content=clean_content,
            source_type=source_type, source_id=clean_source, confidence=confidence, expires_at=expiry,
        )
        with self._connect() as con:
            self._require_agent(con, safe_id)
            con.execute(
                """
                INSERT INTO agent_memories(
                    memory_id, agent_id, content, source_type, source_id, confidence,
                    created_at, expires_at, last_accessed_at, access_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.memory_id, entry.agent_id, entry.content, entry.source_type, entry.source_id,
                    entry.confidence, entry.created_at, entry.expires_at, entry.last_accessed_at, entry.access_count,
                ),
            )
            self._append_event(con, safe_id, "experience_recorded", None, None, "", {"memory_id": entry.memory_id, "source_type": source_type})
        return entry

    def recall(self, agent_id: str, query: str, *, limit: int = 10, at: str | None = None) -> tuple[AgentMemoryEntry, ...]:
        """Return active experience ranked lexically, updating access provenance."""
        safe_id = _safe_agent_id(agent_id)
        clean_query = query.strip()
        if not clean_query:
            return ()
        stamp = at or now_utc()
        now_dt = _parse_time(stamp)
        with self._connect() as con:
            self._require_agent(con, safe_id)
            rows = con.execute("SELECT * FROM agent_memories WHERE agent_id = ?", (safe_id,)).fetchall()
            scored = [
                (_memory_score(clean_query, row["content"], float(row["confidence"])), row)
                for row in rows if _parse_time(row["expires_at"]) > now_dt
            ]
            scored = [item for item in scored if item[0] > 0]
            scored.sort(key=lambda item: (item[0], item[1]["created_at"], item[1]["memory_id"]), reverse=True)
            selected = scored[:max(1, min(limit, 50))]
            for _, row in selected:
                con.execute(
                    "UPDATE agent_memories SET last_accessed_at = ?, access_count = access_count + 1 WHERE memory_id = ?",
                    (stamp, row["memory_id"]),
                )
        return tuple(_memory_from_row(row, last_accessed=stamp, access_delta=1) for _, row in selected)

    def compact_memories(self, agent_id: str, *, at: str | None = None) -> CompactionResult:
        """Archive expired low-confidence experience and retain a bounded summary."""
        safe_id = _safe_agent_id(agent_id)
        stamp = at or now_utc()
        current = _parse_time(stamp)
        with self._connect() as con:
            self._require_agent(con, safe_id)
            rows = con.execute("SELECT * FROM agent_memories WHERE agent_id = ?", (safe_id,)).fetchall()
            candidates = [
                row for row in rows
                if float(row["confidence"]) < 0.3 and _parse_time(row["expires_at"]) <= current
            ]
            if not candidates:
                return CompactionResult(0, None)
            snippets = [str(row["content"])[:280] for row in candidates[:12]]
            summary = "Historical low-confidence experience: " + " | ".join(snippets)
            summary = summary[:7_800]
            summary_entry = AgentMemoryEntry(
                memory_id=f"memory-{uuid.uuid4().hex}", agent_id=safe_id, content=summary,
                source_type="compaction", source_id=f"compaction:{stamp}", confidence=0.5,
                expires_at=_at(90),
            )
            for row in candidates:
                con.execute(
                    """
                    INSERT INTO agent_memory_archive(
                        memory_id, agent_id, content, source_type, source_id, confidence,
                        created_at, expires_at, last_accessed_at, access_count, archived_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*_memory_values(_memory_from_row(row)), stamp),
                )
                con.execute("DELETE FROM agent_memories WHERE memory_id = ?", (row["memory_id"],))
            con.execute(
                """
                INSERT INTO agent_memories(
                    memory_id, agent_id, content, source_type, source_id, confidence,
                    created_at, expires_at, last_accessed_at, access_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _memory_values(summary_entry),
            )
            self._append_event(con, safe_id, "experience_compacted", None, None, "", {"archived": len(candidates), "summary_memory_id": summary_entry.memory_id})
        return CompactionResult(len(candidates), summary_entry.memory_id)

    def context_pack(
        self,
        agent_id: str,
        task: str,
        *,
        mission_id: str = "",
        max_tokens: int = 2_000,
    ) -> TeamContextPack:
        """Assemble bounded repo pointers, conventions, tests, and role experience."""
        agent = self.get(agent_id)
        clean_task = task.strip()
        if not clean_task:
            raise ValueError("context pack requires a task")
        if not 64 <= max_tokens <= 32_000:
            raise ValueError("context pack max_tokens must be between 64 and 32,000")
        items: list[ContextItem] = []
        from .project_memory import load_project_memory
        from .repo_map import get_index

        instructions = load_project_memory(self.root)
        if instructions is not None:
            name, text = instructions
            items.append(ContextItem(kind="convention", reference=name, content=text, relevance=1.0))
        index = get_index(self.root)
        hits = index.search(clean_task, 10)
        top_score = hits[0][0] if hits else 0.0
        for score, document in hits[:5]:
            outline = ", ".join(f"{symbol}:{line}" for symbol, line in document.symbols[:12])
            kind: ContextItemKind = "test" if "test" in Path(document.path).name.lower() else "file"
            items.append(ContextItem(
                kind=kind, reference=document.path, content=(f"Symbols: {outline}" if outline else "Relevant repository file."),
                relevance=round(min(1.0, score / top_score), 4) if top_score else 0.0,
            ))
        for memory in self.recall(agent.agent_id, clean_task, limit=10):
            items.append(ContextItem(
                kind="experience", reference=f"{memory.source_type}:{memory.source_id}",
                content=memory.content, relevance=memory.confidence,
            ))
        return _fit_context_pack(agent, clean_task, mission_id.strip(), items, max_tokens)

    def audit_events(self, *, agent_id: str | None = None, limit: int = 100) -> tuple[TeamAuditEvent, ...]:
        safe_id = _safe_agent_id(agent_id) if agent_id else None
        query = "SELECT * FROM agent_events"
        params: tuple[object, ...] = ()
        if safe_id:
            query += " WHERE agent_id = ?"
            params = (safe_id,)
        query += " ORDER BY sequence DESC LIMIT ?"
        with self._connect() as con:
            rows = con.execute(query, (*params, max(1, min(limit, 500)))).fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def verify_audit(self) -> TeamAuditReport:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM agent_events ORDER BY sequence").fetchall()
        previous = ""
        for expected, row in enumerate(rows, start=1):
            event = _event_from_row(row)
            if event.sequence != expected:
                return TeamAuditReport(valid=False, events=expected - 1, error=f"unexpected sequence at event {expected}")
            if event.previous_hash != previous:
                return TeamAuditReport(valid=False, events=expected - 1, error=f"broken hash chain at event {expected}")
            if event.event_hash != _audit_hash(event):
                return TeamAuditReport(valid=False, events=expected - 1, error=f"hash mismatch at event {expected}")
            previous = event.event_hash
        return TeamAuditReport(valid=True, events=len(rows))

    def _transition(
        self,
        agent_id: str,
        target: AgentStatus,
        *,
        kind: str,
        mission_id: str | None = None,
        updates: dict[str, object] | None = None,
    ) -> PersistentAgent:
        safe_id = _safe_agent_id(agent_id)
        updates = updates or {}
        with self._connect() as con:
            agent = self._require_agent(con, safe_id)
            if target not in _TRANSITIONS[agent.status]:
                raise ValueError(f"persistent agent cannot transition from {agent.status} to {target}")
            current_mission = mission_id if mission_id is not None else agent.current_mission_id or ""
            values = {
                "status": target,
                "current_mission_id": agent.current_mission_id,
                "current_task": agent.current_task,
                "operator_summary": agent.operator_summary,
                "health_check_at": now_utc(),
            }
            values.update(updates)
            stamp = now_utc()
            con.execute(
                """
                UPDATE persistent_agents
                SET status = ?, current_mission_id = ?, current_task = ?, operator_summary = ?,
                    health_check_at = ?, updated_at = ?
                WHERE agent_id = ?
                """,
                (
                    values["status"], values["current_mission_id"], values["current_task"],
                    values["operator_summary"], values["health_check_at"], stamp, safe_id,
                ),
            )
            self._append_event(con, safe_id, kind, agent.status, target, current_mission, updates)
        return self.get(safe_id)

    def _require_agent(self, con: sqlite3.Connection, agent_id: str) -> PersistentAgent:
        row = con.execute("SELECT * FROM persistent_agents WHERE agent_id = ?", (agent_id,)).fetchone()
        if row is None:
            raise ValueError(f"persistent agent {agent_id!r} was not found")
        return _agent_from_row(row)

    def _append_event(
        self,
        con: sqlite3.Connection,
        agent_id: str,
        kind: str,
        from_status: AgentStatus | None,
        to_status: AgentStatus | None,
        mission_id: str,
        payload: object,
    ) -> None:
        previous_row = con.execute("SELECT event_hash FROM agent_events ORDER BY sequence DESC LIMIT 1").fetchone()
        previous = str(previous_row["event_hash"]) if previous_row else ""
        payload_digest = _digest(payload)
        occurred_at = now_utc()
        event_id = _digest(f"{agent_id}:{kind}:{occurred_at}:{uuid.uuid4().hex}")
        partial = TeamAuditEvent(
            sequence=int(con.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS next FROM agent_events").fetchone()["next"]),
            event_id=event_id, agent_id=agent_id, kind=kind, from_status=from_status, to_status=to_status,
            mission_id=mission_id, occurred_at=occurred_at, payload_digest=payload_digest,
            previous_hash=previous, event_hash="0" * 64,
        )
        event = partial.model_copy(update={"event_hash": _audit_hash(partial)})
        con.execute(
            """
            INSERT INTO agent_events(
                sequence, event_id, agent_id, kind, from_status, to_status, mission_id,
                occurred_at, payload_digest, previous_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.sequence, event.event_id, event.agent_id, event.kind, event.from_status,
                event.to_status, event.mission_id, event.occurred_at, event.payload_digest,
                event.previous_hash, event.event_hash,
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=5.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            version = int(con.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, TEAM_SCHEMA_VERSION}:
                raise ValueError(f"unsupported persistent-agent schema version {version}")
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS persistent_agents (
                    agent_id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_mission_id TEXT,
                    current_task TEXT NOT NULL,
                    operator_summary TEXT NOT NULL,
                    health_check_at TEXT NOT NULL,
                    restart_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_memories (
                    memory_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL REFERENCES persistent_agents(agent_id),
                    content TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL,
                    access_count INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_memories_agent_expiry
                    ON agent_memories(agent_id, expires_at);
                CREATE TABLE IF NOT EXISTS agent_memory_archive (
                    memory_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL,
                    access_count INTEGER NOT NULL,
                    archived_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    agent_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT,
                    mission_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                );
                """
            )
            con.execute(f"PRAGMA user_version = {TEAM_SCHEMA_VERSION}")


def _safe_agent_id(agent_id: str) -> str:
    safe = agent_id.strip()
    if not _AGENT_ID.fullmatch(safe):
        raise ValueError("invalid persistent agent id")
    return safe


def _agent_from_row(row: sqlite3.Row) -> PersistentAgent:
    return PersistentAgent(
        agent_id=row["agent_id"], role=row["role"], status=row["status"],
        current_mission_id=row["current_mission_id"], current_task=row["current_task"],
        operator_summary=row["operator_summary"], health_check_at=row["health_check_at"],
        restart_count=row["restart_count"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _memory_from_row(row: sqlite3.Row, *, last_accessed: str | None = None, access_delta: int = 0) -> AgentMemoryEntry:
    return AgentMemoryEntry(
        memory_id=row["memory_id"], agent_id=row["agent_id"], content=row["content"],
        source_type=row["source_type"], source_id=row["source_id"], confidence=row["confidence"],
        created_at=row["created_at"], expires_at=row["expires_at"],
        last_accessed_at=last_accessed or row["last_accessed_at"], access_count=row["access_count"] + access_delta,
    )


def _memory_values(entry: AgentMemoryEntry) -> tuple[object, ...]:
    return (
        entry.memory_id, entry.agent_id, entry.content, entry.source_type, entry.source_id,
        entry.confidence, entry.created_at, entry.expires_at, entry.last_accessed_at, entry.access_count,
    )


def _event_from_row(row: sqlite3.Row) -> TeamAuditEvent:
    return TeamAuditEvent(
        sequence=row["sequence"], event_id=row["event_id"], agent_id=row["agent_id"], kind=row["kind"],
        from_status=row["from_status"], to_status=row["to_status"], mission_id=row["mission_id"],
        occurred_at=row["occurred_at"], payload_digest=row["payload_digest"],
        previous_hash=row["previous_hash"], event_hash=row["event_hash"],
    )


def _default_expiry(source_type: MemorySourceType, confidence: float) -> str:
    if source_type == "workaround":
        return _at(30)
    if source_type == "adr" and confidence >= 0.8:
        return _at(365)
    return _at(90)


def _memory_score(query: str, content: str, confidence: float) -> float:
    query_terms = set(_TOKENS.findall(query.lower()))
    content_terms = set(_TOKENS.findall(content.lower()))
    if not query_terms or not content_terms:
        return 0.0
    lexical = len(query_terms & content_terms) / len(query_terms | content_terms)
    return round(lexical * 0.8 + confidence * 0.2, 6) if lexical else 0.0


def _digest(value: object) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _audit_hash(event: TeamAuditEvent) -> str:
    return _digest(event.model_dump(mode="json", exclude={"event_hash"}))


def _fit_context_pack(
    agent: PersistentAgent,
    task: str,
    mission_id: str,
    items: list[ContextItem],
    max_tokens: int,
) -> TeamContextPack:
    char_budget = max_tokens * 4
    used = len(task)
    selected: list[ContextItem] = []
    was_truncated = False
    for item in sorted(items, key=lambda candidate: candidate.relevance, reverse=True):
        header = f"{item.kind}:{item.reference}\n"
        available = char_budget - used - len(header)
        if available <= 0:
            was_truncated = True
            continue
        content = item.content
        truncated = item.truncated
        if len(content) > available:
            marker = "\n[... truncated]"
            content = content[:max(0, available - len(marker))] + marker
            truncated = True
            was_truncated = True
        selected.append(item.model_copy(update={"content": content, "truncated": truncated}))
        used += len(header) + len(content)
    return TeamContextPack(
        agent_id=agent.agent_id, role=agent.role, mission_id=mission_id, task=task,
        items=selected, estimated_tokens=(used + 3) // 4, max_tokens=max_tokens, truncated=was_truncated,
    )
