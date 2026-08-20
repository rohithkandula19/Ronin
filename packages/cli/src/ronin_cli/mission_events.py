"""Durable, typed, idempotent event delivery for mission state changes.

Mission audit records remain the source of truth. This local event bus is a
portable Phase 0 transport with the same publish/consume contract that a NATS,
Redis Streams, or RabbitMQ adapter can implement later. It writes only compact
metadata and digests, never issue text, raw agent output, or credentials.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import Field

from .mission_models import MissionEvent, StrictModel

try:  # POSIX controllers serialize delivery across API/CLI processes.
    import fcntl
except ImportError:  # pragma: no cover - Windows keeps the in-process lock.
    fcntl = None  # type: ignore[assignment]


MISSION_EVENT_SCHEMA_VERSION = 1
MissionTopic = Literal[
    "mission.created",
    "mission.transitioned",
    "agent.assigned",
    "handoff.completed",
    "test.passed",
    "test.failed",
    "policy.violation",
]
_TOPICS = {
    "mission.created", "mission.transitioned", "agent.assigned", "handoff.completed",
    "test.passed", "test.failed", "policy.violation",
}


class MissionEventPayload(StrictModel):
    """Safe metadata for a mission event; sensitive content stays in local artifacts."""

    audit_kind: str = Field(min_length=1, max_length=100)
    from_stage: str = Field(default="", max_length=100)
    to_stage: str = Field(default="", max_length=100)
    artifact_kind: str = Field(default="", max_length=100)
    payload_digest: str = Field(default="", max_length=128)


class MissionBusEvent(StrictModel):
    """Versioned event envelope for inter-agent and control-plane consumers."""

    schema_version: Literal[MISSION_EVENT_SCHEMA_VERSION] = MISSION_EVENT_SCHEMA_VERSION
    id: str = Field(min_length=64, max_length=64)
    topic: MissionTopic
    mission_id: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=1)
    occurred_at: str = Field(min_length=1, max_length=100)
    producer: str = Field(min_length=1, max_length=100)
    correlation_id: str = Field(min_length=1, max_length=200)
    causation_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=500)
    payload: MissionEventPayload
    previous_hash: str = Field(default="", max_length=128)
    event_hash: str = Field(min_length=64, max_length=128)


@dataclass(frozen=True)
class MissionEventBusReport:
    valid: bool
    events: int
    error: str | None = None


class MissionEventBus:
    """Append-only local transport with idempotent publish and replay support."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.directory = self.root / ".ronin" / "mission-events"
        self.path = self.directory / "events.jsonl"
        self.index_path = self.directory / "idempotency.json"
        self._lock = threading.RLock()

    def publish_audit(self, mission_id: str, audit: MissionEvent) -> tuple[MissionBusEvent, ...]:
        """Map one immutable mission audit record to its typed bus topics."""
        payload = MissionEventPayload(
            audit_kind=audit.kind,
            from_stage=audit.from_stage.value if audit.from_stage else "",
            to_stage=audit.to_stage.value if audit.to_stage else "",
            artifact_kind=audit.artifact_kind,
            payload_digest=audit.payload_digest,
        )
        published = []
        for topic in _topics_for_audit(audit):
            published.append(self.publish(
                topic=topic,
                mission_id=mission_id,
                producer=audit.actor,
                causation_id=audit.event_hash,
                idempotency_key=f"mission:{mission_id}:audit:{audit.event_hash}:{topic}",
                occurred_at=audit.at,
                payload=payload,
            ))
        return tuple(published)

    def publish(
        self,
        *,
        topic: MissionTopic,
        mission_id: str,
        producer: str,
        causation_id: str,
        idempotency_key: str,
        occurred_at: str,
        payload: MissionEventPayload,
    ) -> MissionBusEvent:
        """Append one event, or return the prior envelope for the same key."""
        if topic not in _TOPICS or not mission_id.strip() or not producer.strip() or not causation_id.strip():
            raise ValueError("mission event envelope is invalid")
        if not idempotency_key.strip() or len(idempotency_key) > 500:
            raise ValueError("mission event idempotency key is invalid")
        with self._operation_lock():
            events = self._load_events()
            report = _verify_event_chain(events)
            if not report.valid:
                raise ValueError(f"mission event bus is invalid; refusing publish: {report.error}")
            index = self._load_index()
            existing = next((event for event in events if event.idempotency_key == idempotency_key), None)
            if existing is not None:
                if index.get(idempotency_key) != existing.id:
                    self._write_index({item.idempotency_key: item.id for item in events})
                return existing
            previous = events[-1].event_hash if events else ""
            partial = MissionBusEvent(
                id=_sha256(idempotency_key), topic=topic, mission_id=mission_id.strip(),
                sequence=len(events) + 1, occurred_at=occurred_at, producer=producer.strip(),
                correlation_id=mission_id.strip(), causation_id=causation_id.strip(),
                idempotency_key=idempotency_key.strip(), payload=payload, previous_hash=previous,
                event_hash="0" * 64,
            )
            event = partial.model_copy(update={"event_hash": _event_hash(partial)})
            self._append(event)
            self._write_index({item.idempotency_key: item.id for item in [*events, event]})
            return event

    def events(
        self,
        *,
        mission_id: str | None = None,
        topic: MissionTopic | None = None,
        limit: int = 100,
    ) -> tuple[MissionBusEvent, ...]:
        if topic is not None and topic not in _TOPICS:
            raise ValueError("unknown mission event topic")
        events = self._load_events()
        filtered = [
            event for event in events
            if (mission_id is None or event.mission_id == mission_id)
            and (topic is None or event.topic == topic)
        ]
        filtered.sort(key=lambda event: event.sequence, reverse=True)
        return tuple(filtered[:max(1, limit)])

    def verify(self) -> MissionEventBusReport:
        try:
            events = self._load_events()
        except ValueError as exc:
            return MissionEventBusReport(False, 0, str(exc))
        return _verify_event_chain(events)

    def _load_events(self) -> list[MissionBusEvent]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise ValueError("mission event log could not be read") from exc
        events: list[MissionBusEvent] = []
        for index, line in enumerate(lines, start=1):
            try:
                events.append(MissionBusEvent.model_validate_json(line))
            except ValueError as exc:
                raise ValueError(f"invalid mission event at line {index}") from exc
        return events

    def _append(self, event: MissionBusEvent) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _write_index(self, index: dict[str, str]) -> None:
        _atomic_write(self.index_path, json.dumps(index, indent=2, sort_keys=True) + "\n")

    def _load_index(self) -> dict[str, str]:
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            key: value for key, value in raw.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    @contextmanager
    def _operation_lock(self) -> Iterator[None]:
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            lock_path = self.directory / ".controller.lock"
            with lock_path.open("a+", encoding="utf-8") as handle:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _verify_event_chain(events: list[MissionBusEvent]) -> MissionEventBusReport:
    previous = ""
    seen_keys: set[str] = set()
    for expected, event in enumerate(events, start=1):
        if event.sequence != expected:
            return MissionEventBusReport(False, expected - 1, f"unexpected event sequence at line {expected}")
        if event.previous_hash != previous:
            return MissionEventBusReport(False, expected - 1, f"broken event hash chain at line {expected}")
        if event.event_hash != _event_hash(event):
            return MissionEventBusReport(False, expected - 1, f"event hash mismatch at line {expected}")
        if event.idempotency_key in seen_keys:
            return MissionEventBusReport(False, expected - 1, f"duplicate idempotency key at line {expected}")
        seen_keys.add(event.idempotency_key)
        previous = event.event_hash
    return MissionEventBusReport(True, len(events))


def _topics_for_audit(audit: MissionEvent) -> tuple[MissionTopic, ...]:
    if audit.kind == "mission_created":
        return ("mission.created",)
    if audit.kind == "candidate_workspace_attached":
        return ("agent.assigned",)
    if audit.kind == "artifacts_recorded":
        return ("handoff.completed",)
    if audit.kind == "stage_transition":
        if audit.from_stage and audit.from_stage.value == "testing":
            if audit.to_stage and audit.to_stage.value == "reviewing":
                return ("mission.transitioned", "test.passed")
            return ("mission.transitioned", "test.failed")
        if audit.to_stage and audit.to_stage.value == "failed" and "security" in audit.actor:
            return ("mission.transitioned", "policy.violation")
    return ("mission.transitioned",)


def _event_hash(event: MissionBusEvent) -> str:
    return _sha256(json.dumps(
        event.model_dump(mode="json", exclude={"event_hash"}), sort_keys=True, separators=(",", ":"),
    ))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
