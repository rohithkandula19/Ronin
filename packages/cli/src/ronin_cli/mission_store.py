"""Durable mission records with a hash-chained append-only audit log."""
from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .mission_models import (
    MissionArtifacts,
    MissionBudget,
    MissionEvent,
    MissionRecord,
    MissionSpec,
    MissionStage,
    transition_record,
)


MISSION_SCHEMA_VERSION = 1
_MISSION_ID = re.compile(r"^mission-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return f"mission-{_datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


@dataclass(frozen=True)
class MissionAuditReport:
    valid: bool
    events: int
    error: str | None = None


class MissionStore:
    """Project-local state snapshot plus append-only event evidence for missions."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.directory = self.root / ".ronin" / "missions"
        self._lock = threading.RLock()

    def create(self, spec: MissionSpec, *, budget: MissionBudget | None = None) -> MissionRecord:
        with self._lock:
            record = MissionRecord(id=_new_id(), spec=spec, budget=budget or MissionBudget())
            event = self._event(
                record, actor="operator", kind="mission_created", summary=spec.title,
                from_stage=None, to_stage=MissionStage.PENDING,
            )
            record = record.model_copy(update={
                "event_count": event.sequence,
                "last_event_hash": event.event_hash,
                "updated": _now(),
            })
            self._append_event(record.id, event)
            self._save(record)
            return record

    def list(self, *, limit: int = 100) -> tuple[MissionRecord, ...]:
        try:
            paths = sorted(self.directory.glob("mission-*.json"))
        except OSError:
            return ()
        records: list[MissionRecord] = []
        for path in paths:
            try:
                records.append(self.load(path.stem))
            except ValueError:
                continue
        records.sort(key=lambda record: (record.updated, record.id), reverse=True)
        return tuple(records[:max(1, limit)])

    def load(self, mission_id: str) -> MissionRecord:
        safe_id = _safe_id(mission_id)
        try:
            raw = json.loads((self.directory / f"{safe_id}.json").read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != MISSION_SCHEMA_VERSION:
                raise ValueError("unsupported mission schema")
            return MissionRecord.model_validate(raw.get("mission"))
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"mission {safe_id!r} was not found or is invalid") from exc

    def transition(
        self,
        mission_id: str,
        target: MissionStage,
        *,
        actor: str = "operator",
        summary: str = "",
    ) -> MissionRecord:
        with self._lock:
            record = self.load(mission_id)
            advanced = transition_record(record, target)
            event = self._event(
                record, actor=actor, kind="stage_transition", summary=summary,
                from_stage=record.stage, to_stage=target,
            )
            advanced = advanced.model_copy(update={
                "event_count": event.sequence,
                "last_event_hash": event.event_hash,
                "updated": _now(),
            })
            self._append_event(record.id, event)
            self._save(advanced)
            return advanced

    def set_candidate_workspace(self, mission_id: str, workspace_id: str) -> MissionRecord:
        if not workspace_id.strip():
            raise ValueError("candidate workspace id must not be empty")
        with self._lock:
            record = self.load(mission_id)
            updated = record.model_copy(update={
                "candidate_workspace_id": workspace_id.strip(), "updated": _now(),
            })
            event = self._event(
                record, actor="workspace-service", kind="candidate_workspace_attached",
                summary=workspace_id.strip(), artifact_kind="candidate_workspace",
            )
            updated = updated.model_copy(update={
                "event_count": event.sequence, "last_event_hash": event.event_hash,
            })
            self._append_event(record.id, event)
            self._save(updated)
            return updated

    def save_artifacts(self, mission_id: str, artifacts: MissionArtifacts) -> MissionRecord:
        """Replace the typed artifact bundle and retain only its digest in the audit log."""
        with self._lock:
            record = self.load(mission_id)
            digest = _digest(artifacts.model_dump(mode="json"))
            updated = record.model_copy(update={"artifacts": artifacts, "updated": _now()})
            event = self._event(
                record, actor="agent", kind="artifacts_recorded", artifact_kind="mission_artifacts",
                payload_digest=digest,
            )
            updated = updated.model_copy(update={
                "event_count": event.sequence, "last_event_hash": event.event_hash,
            })
            self._append_event(record.id, event)
            self._save(updated)
            return updated

    def events(self, mission_id: str) -> tuple[MissionEvent, ...]:
        safe_id = _safe_id(mission_id)
        path = self._events_path(safe_id)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ()
        events: list[MissionEvent] = []
        for line in lines:
            try:
                events.append(MissionEvent.model_validate_json(line))
            except ValueError:
                return ()
        return tuple(events)

    def verify_audit(self, mission_id: str) -> MissionAuditReport:
        safe_id = _safe_id(mission_id)
        try:
            lines = self._events_path(safe_id).read_text(encoding="utf-8").splitlines()
        except OSError:
            return MissionAuditReport(False, 0, "mission audit log was not found")
        previous = ""
        for index, line in enumerate(lines, start=1):
            try:
                event = MissionEvent.model_validate_json(line)
            except ValueError:
                return MissionAuditReport(False, index - 1, f"invalid event at line {index}")
            if event.sequence != index:
                return MissionAuditReport(False, index - 1, f"unexpected event sequence at line {index}")
            if event.previous_hash != previous:
                return MissionAuditReport(False, index - 1, f"broken hash chain at line {index}")
            if event.event_hash != _event_hash(event):
                return MissionAuditReport(False, index - 1, f"event hash mismatch at line {index}")
            previous = event.event_hash
        try:
            record = self.load(safe_id)
        except ValueError:
            return MissionAuditReport(False, len(lines), "mission snapshot is unavailable or invalid")
        if record.event_count != len(lines) or record.last_event_hash != previous:
            return MissionAuditReport(False, len(lines), "mission snapshot does not match the audit chain")
        return MissionAuditReport(True, len(lines))

    def _event(
        self,
        record: MissionRecord,
        *,
        actor: str,
        kind: str,
        summary: str = "",
        artifact_kind: str = "",
        payload_digest: str = "",
        from_stage: MissionStage | None = None,
        to_stage: MissionStage | None = None,
    ) -> MissionEvent:
        partial = MissionEvent(
            sequence=record.event_count + 1,
            actor=actor,
            kind=kind,
            summary=summary,
            artifact_kind=artifact_kind,
            payload_digest=payload_digest,
            previous_hash=record.last_event_hash,
            from_stage=from_stage,
            to_stage=to_stage,
            event_hash="0" * 64,
        )
        return partial.model_copy(update={"event_hash": _event_hash(partial)})

    def _save(self, record: MissionRecord) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{record.id}.json"
        payload = {"schema_version": MISSION_SCHEMA_VERSION, "mission": record.model_dump(mode="json")}
        _atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def _append_event(self, mission_id: str, event: MissionEvent) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self._events_path(mission_id).open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _events_path(self, mission_id: str) -> Path:
        return self.directory / f"{mission_id}.events.jsonl"


def _event_hash(event: MissionEvent) -> str:
    payload = event.model_dump(mode="json", exclude={"event_hash"})
    return _digest(payload)


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _safe_id(value: str) -> str:
    safe = Path(value).name
    if safe != value or not _MISSION_ID.fullmatch(safe):
        raise ValueError("invalid mission id")
    return safe


def _atomic_write(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
