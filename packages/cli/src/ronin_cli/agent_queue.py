"""Project-local queue for controlled agent orchestration.

The scheduler answers *when* a prompt is due; this queue answers *which* agent
goal may run next. Jobs live in ``.ronin/agent-queue.json`` beside the project,
are claimed explicitly by a worker, and keep their terminal outcome. The queue
does not start background processes or bypass approval: it is a durable handoff
between a user, cron/CI, and ``ronin util agent-queue run-next``.
"""
from __future__ import annotations

import datetime as _datetime
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


SCHEMA_VERSION = 1
_TERMINAL = {"completed", "failed", "cancelled"}
_STATES = {"queued", "running", *_TERMINAL}


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return f"job-{_datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


@dataclass(frozen=True)
class AgentQueueJob:
    id: str
    goal: str
    write: bool
    created: str
    status: str = "queued"
    attempts: int = 0
    updated: str = ""
    error: str | None = None
    run_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> "AgentQueueJob | None":
        if not isinstance(raw, dict):
            return None
        job_id = raw.get("id")
        goal = raw.get("goal")
        status = raw.get("status", "queued")
        if not isinstance(job_id, str) or not job_id or not isinstance(goal, str) or not goal.strip():
            return None
        if status not in _STATES:
            return None
        return cls(
            id=job_id,
            goal=goal,
            write=bool(raw.get("write", False)),
            created=str(raw.get("created", "")),
            status=status,
            attempts=max(0, int(raw.get("attempts", 0) or 0)),
            updated=str(raw.get("updated", raw.get("created", ""))),
            error=raw.get("error") if isinstance(raw.get("error"), str) else None,
            run_id=raw.get("run_id") if isinstance(raw.get("run_id"), str) else None,
        )


class AgentQueue:
    """Atomic JSON queue for one project.

    A worker claims one queued job before executing it. The intentionally small
    state machine prevents a completed job from being accidentally run again;
    retrying is an explicit new enqueue so the audit trail stays honest.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.path = self.root / ".ronin" / "agent-queue.json"

    def list(self) -> list[AgentQueueJob]:
        return self._load()

    def enqueue(self, goal: str, *, write: bool = False) -> AgentQueueJob:
        text = goal.strip()
        if not text:
            raise ValueError("agent queue goal must not be empty")
        now = _now()
        job = AgentQueueJob(id=_new_id(), goal=text, write=write, created=now, updated=now)
        jobs = self._load()
        jobs.append(job)
        self._save(jobs)
        return job

    def claim_next(self) -> AgentQueueJob | None:
        jobs = self._load()
        for index, job in enumerate(jobs):
            if job.status != "queued":
                continue
            claimed = AgentQueueJob(
                **{**job.to_dict(), "status": "running", "attempts": job.attempts + 1,
                   "updated": _now(), "error": None},
            )
            jobs[index] = claimed
            self._save(jobs)
            return claimed
        return None

    def finish(
        self,
        job_id: str,
        *,
        success: bool,
        error: str | None = None,
        run_id: str | None = None,
    ) -> AgentQueueJob | None:
        jobs = self._load()
        for index, job in enumerate(jobs):
            if job.id != job_id or job.status != "running":
                continue
            finished = AgentQueueJob(
                **{**job.to_dict(), "status": "completed" if success else "failed",
                   "updated": _now(), "error": (error or None), "run_id": run_id},
            )
            jobs[index] = finished
            self._save(jobs)
            return finished
        return None

    def cancel(self, job_id: str) -> AgentQueueJob | None:
        jobs = self._load()
        for index, job in enumerate(jobs):
            if job.id != job_id or job.status != "queued":
                continue
            cancelled = AgentQueueJob(
                **{**job.to_dict(), "status": "cancelled", "updated": _now()},
            )
            jobs[index] = cancelled
            self._save(jobs)
            return cancelled
        return None

    def _load(self) -> list[AgentQueueJob]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
            return []
        jobs = [AgentQueueJob.from_dict(item) for item in raw.get("jobs", [])]
        return [job for job in jobs if job is not None]

    def _save(self, jobs: list[AgentQueueJob]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "updated": _now(),
            "jobs": [job.to_dict() for job in jobs],
        }
        fd, temp_name = tempfile.mkstemp(prefix=".agent-queue-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
            os.replace(temp_name, self.path)
        except OSError:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
