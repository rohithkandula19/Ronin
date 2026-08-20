"""Leased, sandboxed remote verification workers for durable missions.

The control plane stores an immutable candidate patch and dispatches it only to
an authenticated worker. A worker clones the public HTTPS repository at the
recorded revision, applies that patch, and runs the approved command in Docker
with no network. The worker cannot access the controller checkout, stage code,
push, or publish a pull request. It returns compact execution evidence only.
"""
from __future__ import annotations

import datetime as _datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterator

try:  # POSIX controller hosts get inter-process lease serialization.
    import fcntl
except ImportError:  # pragma: no cover - Windows still retains the in-process lock.
    fcntl = None  # type: ignore[assignment]

from .candidate_workspace import CandidateCommandResult, CandidateWorkspaceService
from .mission_models import MissionRecord, MissionStage
from .mission_store import MissionStore
from .mission_workflow import MissionWorkflow


REMOTE_WORKER_SCHEMA_VERSION = 1
_JOB_ID = re.compile(r"^remote-job-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
_MISSION_ID = re.compile(r"^mission-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
_CANDIDATE_ID = re.compile(r"^candidate-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
_GIT_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_LEASE_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STATUSES = {"queued", "leased", "completed", "failed", "cancelled"}
_OUTCOMES = {"passed", "failed", "blocked", "timed_out"}
_MAX_PATCH_BYTES = 1_000_000
_MAX_COMMAND = 20_000
_MAX_SUMMARY = 2_000
_MAX_TIMEOUT = 3_600
_MIN_LEASE = 60
_MAX_LEASE = 7_200


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds")


def _new_job_id() -> str:
    stamp = _datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"remote-job-{stamp}-{uuid.uuid4().hex[:6]}"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_worker_id(worker_id: str) -> str:
    value = worker_id.strip()
    if not _WORKER_ID.fullmatch(value):
        raise ValueError("worker id must use 1-80 letters, numbers, dots, underscores, or dashes")
    return value


def _safe_repository_url(value: str) -> str:
    text = value.strip()
    parsed = urllib.parse.urlparse(text)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.strip("/")
    ):
        raise ValueError("remote worker repository URL must be credential-free https")
    return text


def _safe_endpoint(value: str) -> str:
    text = value.rstrip("/")
    parsed = urllib.parse.urlparse(text)
    localhost = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme == "https" and parsed.netloc:
        return text
    if parsed.scheme == "http" and parsed.netloc and localhost:
        return text
    raise ValueError("remote worker endpoint must use https (http is allowed only for localhost)")


def _is_expired(value: str | None) -> bool:
    if not value:
        return True
    try:
        expires = _datetime.datetime.fromisoformat(value)
    except ValueError:
        return True
    return expires <= _datetime.datetime.now(_datetime.timezone.utc)


@dataclass(frozen=True)
class RemoteWorkerEvidence:
    """Bounded result returned by a worker; logs never enter the control plane."""

    outcome: str
    exit_code: int | None = None
    duration_seconds: float = 0.0
    summary: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in _OUTCOMES:
            raise ValueError("remote worker outcome is invalid")
        if self.exit_code is not None and not isinstance(self.exit_code, int):
            raise ValueError("remote worker exit code is invalid")
        if self.outcome == "passed" and self.exit_code != 0:
            raise ValueError("a passed remote worker result must have exit code 0")
        if self.outcome == "failed" and (self.exit_code is None or self.exit_code == 0):
            raise ValueError("a failed remote worker result must have a nonzero exit code")
        if not 0.0 <= self.duration_seconds <= 86_400.0:
            raise ValueError("remote worker duration is invalid")
        if len(self.summary) > _MAX_SUMMARY:
            raise ValueError("remote worker summary exceeds the safety limit")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> "RemoteWorkerEvidence | None":
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                outcome=str(raw.get("outcome", "")),
                exit_code=raw.get("exit_code") if isinstance(raw.get("exit_code"), int) else None,
                duration_seconds=float(raw.get("duration_seconds", 0.0)),
                summary=str(raw.get("summary", "")),
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class RemoteWorkerJob:
    """An immutable remote candidate verification request plus lease state."""

    id: str
    mission_id: str
    candidate_id: str
    repository_url: str
    base_revision: str
    patch: str
    patch_digest: str
    image: str
    command: str
    timeout_seconds: int
    created: str
    updated: str
    status: str = "queued"
    attempts: int = 0
    worker_id: str | None = None
    lease_id: str | None = None
    lease_token_digest: str | None = None
    lease_expires: str | None = None
    evidence: RemoteWorkerEvidence | None = None
    error: str | None = None

    def as_dict(self, *, include_patch: bool = True) -> dict[str, object]:
        data = asdict(self)
        data["evidence"] = self.evidence.as_dict() if self.evidence else None
        if not include_patch:
            data.pop("patch", None)
            data.pop("lease_token_digest", None)
        return data

    @classmethod
    def from_dict(cls, raw: object) -> "RemoteWorkerJob | None":
        if not isinstance(raw, dict):
            return None
        evidence = RemoteWorkerEvidence.from_dict(raw.get("evidence")) if raw.get("evidence") is not None else None
        values = ("id", "mission_id", "candidate_id", "repository_url", "base_revision", "patch", "patch_digest", "image", "command", "created", "updated")
        if (
            not all(isinstance(raw.get(field), str) for field in values)
            or not isinstance(raw.get("timeout_seconds"), int)
            or not isinstance(raw.get("status", "queued"), str)
            or not isinstance(raw.get("attempts", 0), int)
            or not _valid_optional_strings(
                raw.get("worker_id"), raw.get("lease_id"), raw.get("lease_token_digest"),
                raw.get("lease_expires"), raw.get("error"),
            )
        ):
            return None
        try:
            job = cls(
                id=raw["id"], mission_id=raw["mission_id"], candidate_id=raw["candidate_id"],
                repository_url=raw["repository_url"], base_revision=raw["base_revision"], patch=raw["patch"],
                patch_digest=raw["patch_digest"], image=raw["image"], command=raw["command"],
                timeout_seconds=raw.get("timeout_seconds"), created=raw["created"], updated=raw["updated"],
                status=raw.get("status", "queued"), attempts=raw.get("attempts", 0),
                worker_id=raw.get("worker_id"), lease_id=raw.get("lease_id"),
                lease_token_digest=raw.get("lease_token_digest"), lease_expires=raw.get("lease_expires"),
                evidence=evidence, error=raw.get("error"),
            )
        except (TypeError, ValueError):
            return None
        if (
            not _JOB_ID.fullmatch(job.id)
            or not _MISSION_ID.fullmatch(job.mission_id)
            or not _CANDIDATE_ID.fullmatch(job.candidate_id)
            or not _GIT_REVISION.fullmatch(job.base_revision)
            or job.status not in _STATUSES
            or not 1 <= job.timeout_seconds <= _MAX_TIMEOUT
            or not _SHA256.fullmatch(job.patch_digest)
            or _digest(job.patch) != job.patch_digest
            or len(job.patch.encode("utf-8")) > _MAX_PATCH_BYTES
            or not job.image
            or not job.command.strip()
            or len(job.command) > _MAX_COMMAND
            or job.attempts < 0
            or job.worker_id is not None and not _WORKER_ID.fullmatch(job.worker_id)
            or job.lease_id is not None and not _LEASE_ID.fullmatch(job.lease_id)
            or job.lease_token_digest is not None and not _SHA256.fullmatch(job.lease_token_digest)
            or not _valid_optional_strings(job.lease_expires, job.error)
            or raw.get("evidence") is not None and evidence is None
        ):
            return None
        try:
            _safe_repository_url(job.repository_url)
        except ValueError:
            return None
        return job


@dataclass(frozen=True)
class RemoteWorkerDispatch:
    """One-time worker payload. The lease token is never persisted in clear text."""

    job_id: str
    mission_id: str
    candidate_id: str
    repository_url: str
    base_revision: str
    patch: str
    patch_digest: str
    image: str
    command: str
    timeout_seconds: int
    worker_id: str
    lease_id: str
    lease_token: str
    lease_expires: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> "RemoteWorkerDispatch | None":
        if not isinstance(raw, dict):
            return None
        fields = (
            "job_id", "mission_id", "candidate_id", "repository_url", "base_revision", "patch",
            "patch_digest", "image", "command", "worker_id", "lease_id", "lease_token", "lease_expires",
        )
        if not all(isinstance(raw.get(field), str) for field in fields) or not isinstance(raw.get("timeout_seconds"), int):
            return None
        try:
            dispatch = cls(**{field: raw[field] for field in fields}, timeout_seconds=raw["timeout_seconds"])
        except TypeError:
            return None
        if (
            not _JOB_ID.fullmatch(dispatch.job_id)
            or not _MISSION_ID.fullmatch(dispatch.mission_id)
            or not _CANDIDATE_ID.fullmatch(dispatch.candidate_id)
            or not _GIT_REVISION.fullmatch(dispatch.base_revision)
            or not _WORKER_ID.fullmatch(dispatch.worker_id)
            or not _LEASE_ID.fullmatch(dispatch.lease_id)
            or not dispatch.lease_token
            or not _SHA256.fullmatch(dispatch.patch_digest)
            or _digest(dispatch.patch) != dispatch.patch_digest
            or len(dispatch.patch.encode("utf-8")) > _MAX_PATCH_BYTES
            or not 1 <= dispatch.timeout_seconds <= _MAX_TIMEOUT
            or not dispatch.image
            or not dispatch.command.strip()
        ):
            return None
        try:
            _safe_repository_url(dispatch.repository_url)
        except ValueError:
            return None
        return dispatch


class RemoteWorkerAuth:
    """Project-local controller token, stored as a salted digest and shown once."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.path = self.root / ".ronin" / "remote-worker-auth.json"

    def provision(self, *, rotate: bool = False) -> str:
        if self.path.exists() and not rotate:
            raise ValueError("remote worker token already exists; use --rotate after replacing every worker token")
        token = secrets.token_urlsafe(32)
        salt = secrets.token_hex(16)
        _write_json(self.path, {"schema_version": 1, "salt": salt, "digest": _digest(f"{salt}:{token}")})
        return token

    def verify(self, token: str | None) -> bool:
        if not token:
            return False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(raw, dict) or not isinstance(raw.get("salt"), str) or not isinstance(raw.get("digest"), str):
            return False
        return hmac.compare_digest(_digest(f"{raw['salt']}:{token}"), raw["digest"])


class RemoteWorkerStore:
    """Atomic lease store; a stale lease is re-queued before another claim."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.directory = self.root / ".ronin" / "remote-worker-jobs"
        self._lock = threading.RLock()

    def enqueue(
        self,
        *,
        mission_id: str,
        candidate_id: str,
        repository_url: str,
        base_revision: str,
        patch: str,
        image: str,
        command: str,
        timeout_seconds: int,
    ) -> RemoteWorkerJob:
        _safe_repository_url(repository_url)
        if not _MISSION_ID.fullmatch(mission_id) or not _CANDIDATE_ID.fullmatch(candidate_id):
            raise ValueError("remote worker job needs a valid mission and candidate id")
        if not _GIT_REVISION.fullmatch(base_revision):
            raise ValueError("remote worker job needs a committed Git revision")
        if not patch.strip() or len(patch.encode("utf-8")) > _MAX_PATCH_BYTES:
            raise ValueError("remote worker patch must be non-empty and no larger than 1 MB")
        if not command.strip() or len(command) > _MAX_COMMAND:
            raise ValueError("remote worker command must be non-empty and no longer than 20,000 characters")
        if not image.strip() or not 1 <= timeout_seconds <= _MAX_TIMEOUT:
            raise ValueError("remote worker needs a Docker image and a timeout between 1 and 3600 seconds")
        now = _now()
        job = RemoteWorkerJob(
            id=_new_job_id(), mission_id=mission_id, candidate_id=candidate_id,
            repository_url=repository_url, base_revision=base_revision, patch=patch,
            patch_digest=_digest(patch), image=image, command=command.strip(),
            timeout_seconds=timeout_seconds, created=now, updated=now,
        )
        with self._operation_lock():
            self._save(job)
        return job

    def list(self, *, limit: int = 100) -> tuple[RemoteWorkerJob, ...]:
        try:
            paths = sorted(self.directory.glob("remote-job-*.json"))
        except OSError:
            return ()
        jobs: list[RemoteWorkerJob] = []
        for path in paths:
            try:
                jobs.append(self.load(path.stem))
            except ValueError:
                continue
        jobs.sort(key=lambda job: (job.updated, job.id), reverse=True)
        return tuple(jobs[:max(1, limit)])

    def load(self, job_id: str) -> RemoteWorkerJob:
        safe_id = _safe_job_id(job_id)
        try:
            raw = json.loads((self.directory / f"{safe_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"remote worker job {safe_id!r} was not found") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != REMOTE_WORKER_SCHEMA_VERSION:
            raise ValueError(f"remote worker job {safe_id!r} is invalid")
        job = RemoteWorkerJob.from_dict(raw.get("job"))
        if job is None:
            raise ValueError(f"remote worker job {safe_id!r} is invalid")
        return job

    def claim_next(self, worker_id: str) -> RemoteWorkerDispatch | None:
        worker = _safe_worker_id(worker_id)
        with self._operation_lock():
            jobs = list(self.list(limit=10_000))
            recovered = [self._release_expired(job) for job in jobs]
            for changed in recovered:
                original = next(job for job in jobs if job.id == changed.id)
                if changed != original:
                    self._save(changed)
            ready = next((job for job in recovered if job.status == "queued"), None)
            if ready is None:
                return None
            token = secrets.token_urlsafe(32)
            lease_seconds = min(_MAX_LEASE, max(_MIN_LEASE, ready.timeout_seconds + 120))
            expires = (_datetime.datetime.now(_datetime.timezone.utc) + _datetime.timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
            claimed = replace(
                ready, status="leased", attempts=ready.attempts + 1, worker_id=worker,
                lease_id=uuid.uuid4().hex, lease_token_digest=_digest(token), lease_expires=expires,
                updated=_now(), evidence=None, error=None,
            )
            self._save(claimed)
            return RemoteWorkerDispatch(
                job_id=claimed.id, mission_id=claimed.mission_id, candidate_id=claimed.candidate_id,
                repository_url=claimed.repository_url, base_revision=claimed.base_revision, patch=claimed.patch,
                patch_digest=claimed.patch_digest, image=claimed.image, command=claimed.command,
                timeout_seconds=claimed.timeout_seconds, worker_id=worker, lease_id=claimed.lease_id or "",
                lease_token=token, lease_expires=expires,
            )

    def active_lease(self, job_id: str, *, worker_id: str, lease_id: str, lease_token: str) -> RemoteWorkerJob:
        worker = _safe_worker_id(worker_id)
        if not _LEASE_ID.fullmatch(lease_id) or not lease_token:
            raise ValueError("remote worker lease is invalid")
        with self._operation_lock():
            return self._active_lease_unlocked(job_id, worker=worker, lease_id=lease_id, lease_token=lease_token)

    def complete(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_id: str,
        lease_token: str,
        evidence: RemoteWorkerEvidence,
    ) -> RemoteWorkerJob:
        worker = _safe_worker_id(worker_id)
        if not _LEASE_ID.fullmatch(lease_id) or not lease_token:
            raise ValueError("remote worker lease is invalid")
        with self._operation_lock():
            job = self._active_lease_unlocked(job_id, worker=worker, lease_id=lease_id, lease_token=lease_token)
            status = "completed" if evidence.outcome == "passed" else "failed"
            completed = replace(
                job, status=status, updated=_now(), evidence=evidence,
                lease_token_digest=None, lease_expires=None,
                error=None if status == "completed" else evidence.summary[:_MAX_SUMMARY],
            )
            self._save(completed)
            return completed

    def release(self, job_id: str) -> RemoteWorkerJob:
        """Release a lease only after an operator established its worker stopped."""
        with self._operation_lock():
            job = self.load(job_id)
            if job.status != "leased":
                raise ValueError("remote worker job has no active lease")
            released = replace(
                job, status="queued", updated=_now(), worker_id=None, lease_id=None,
                lease_token_digest=None, lease_expires=None, error="operator released interrupted worker lease",
            )
            self._save(released)
            return released

    def _release_expired(self, job: RemoteWorkerJob) -> RemoteWorkerJob:
        if job.status != "leased" or not _is_expired(job.lease_expires):
            return job
        return replace(
            job, status="queued", updated=_now(), worker_id=None, lease_id=None,
            lease_token_digest=None, lease_expires=None, error="worker lease expired before completion",
        )

    def _active_lease_unlocked(
        self,
        job_id: str,
        *,
        worker: str,
        lease_id: str,
        lease_token: str,
    ) -> RemoteWorkerJob:
        job = self.load(job_id)
        if job.status != "leased" or _is_expired(job.lease_expires):
            raise ValueError("remote worker lease is no longer active")
        if job.worker_id != worker or job.lease_id != lease_id or not job.lease_token_digest:
            raise ValueError("remote worker lease does not belong to this worker")
        if not hmac.compare_digest(job.lease_token_digest, _digest(lease_token)):
            raise ValueError("remote worker lease token is invalid")
        return job

    @contextmanager
    def _operation_lock(self) -> Iterator[None]:
        """Serialize lease transitions across controller processes on POSIX."""
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

    def _save(self, job: RemoteWorkerJob) -> None:
        _write_json(self.directory / f"{job.id}.json", {
            "schema_version": REMOTE_WORKER_SCHEMA_VERSION,
            "job": job.as_dict(),
        })


class RemoteVerificationCoordinator:
    """Binds remote execution to the current candidate and mission state."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.jobs = RemoteWorkerStore(self.root)
        self.missions = MissionStore(self.root)
        self.candidates = CandidateWorkspaceService(self.root)
        self.workflow = MissionWorkflow(self.root)

    def enqueue(
        self,
        mission_id: str,
        command: str,
        *,
        timeout_seconds: int = 600,
        repository_url: str | None = None,
    ) -> RemoteWorkerJob:
        mission = self.missions.load(mission_id)
        if mission.stage is not MissionStage.IMPLEMENTING:
            raise ValueError(f"mission must be implementing before remote verification; it is {mission.stage.value}")
        if not mission.candidate_workspace_id:
            raise ValueError("mission has no candidate workspace")
        candidate = self.candidates.load(mission.candidate_workspace_id)
        if candidate.mission_id != mission.id or candidate.status != "active" or not candidate.image:
            raise ValueError("mission needs an active Docker-configured candidate workspace")
        patch = self.candidates.diff(candidate.id)
        url = _safe_repository_url(repository_url) if repository_url else _origin_url(self.root)
        return self.jobs.enqueue(
            mission_id=mission.id, candidate_id=candidate.id, repository_url=url,
            base_revision=candidate.base_revision, patch=patch, image=candidate.image,
            command=command, timeout_seconds=timeout_seconds,
        )

    def complete(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_id: str,
        lease_token: str,
        evidence: RemoteWorkerEvidence,
    ) -> RemoteWorkerJob:
        job = self.jobs.active_lease(job_id, worker_id=worker_id, lease_id=lease_id, lease_token=lease_token)
        effective = evidence
        if not self._dispatch_is_current(job):
            effective = RemoteWorkerEvidence("blocked", summary="candidate changed after remote worker dispatch")
        else:
            result = CandidateCommandResult(
                command=job.command,
                exit_code=(
                    0 if evidence.outcome == "passed" and evidence.exit_code is None
                    else (1 if evidence.outcome == "failed" and evidence.exit_code in {None, 0} else evidence.exit_code)
                ),
                duration_seconds=evidence.duration_seconds,
                timed_out=evidence.outcome == "timed_out",
                blocked=evidence.outcome == "blocked",
                error="" if evidence.outcome == "passed" else evidence.summary[:_MAX_SUMMARY],
            )
            try:
                self.workflow.record_remote_verification(job.mission_id, result, worker_id=worker_id)
            except ValueError:
                effective = RemoteWorkerEvidence("blocked", summary="mission no longer accepts this worker result")
        return self.jobs.complete(
            job.id, worker_id=worker_id, lease_id=lease_id, lease_token=lease_token, evidence=effective,
        )

    def _dispatch_is_current(self, job: RemoteWorkerJob) -> bool:
        try:
            mission = self.missions.load(job.mission_id)
            candidate = self.candidates.load(job.candidate_id)
            return (
                mission.stage is MissionStage.IMPLEMENTING
                and mission.candidate_workspace_id == candidate.id
                and candidate.status == "active"
                and candidate.base_revision == job.base_revision
                and _digest(self.candidates.diff(candidate.id)) == job.patch_digest
            )
        except ValueError:
            return False


def remote_docker_argv(workspace: Path, image: str, command: str) -> list[str]:
    """Docker-only execution contract for a checkout created by a remote worker."""
    return [
        "docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "256", "--memory", "1g",
        "--mount", f"type=bind,source={workspace.resolve()},target=/workspace,rw",
        "--workdir", "/workspace", image, "sh", "-lc", command,
    ]


def execute_remote_dispatch(
    dispatch: RemoteWorkerDispatch,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> RemoteWorkerEvidence:
    """Checkout, patch, and execute one dispatch without running project code on host."""
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="ronin-remote-worker-") as temporary:
            workspace = Path(temporary) / "workspace"
            if not _run_git(runner, ["clone", "--no-checkout", dispatch.repository_url, str(workspace)], timeout=120):
                return _blocked_evidence(started, "remote repository checkout failed")
            if not _run_git(runner, ["-C", str(workspace), "checkout", "--detach", dispatch.base_revision], timeout=120):
                return _blocked_evidence(started, "remote repository revision checkout failed")
            if not _apply_patch(runner, workspace, dispatch.patch):
                return _blocked_evidence(started, "remote candidate patch could not be applied")
            try:
                proc = runner(
                    remote_docker_argv(workspace, dispatch.image, dispatch.command),
                    capture_output=True, text=True, timeout=dispatch.timeout_seconds, check=False,
                )
            except subprocess.TimeoutExpired:
                return RemoteWorkerEvidence("timed_out", duration_seconds=time.monotonic() - started, summary="remote Docker command timed out")
            except OSError:
                return _blocked_evidence(started, "remote Docker command could not start")
    except OSError:
        return _blocked_evidence(started, "remote worker checkout could not be prepared")
    duration = time.monotonic() - started
    if proc.returncode == 0:
        return RemoteWorkerEvidence("passed", exit_code=0, duration_seconds=duration, summary="remote Docker command completed")
    return RemoteWorkerEvidence("failed", exit_code=proc.returncode, duration_seconds=duration, summary="remote Docker command failed")


class RemoteWorkerClient:
    """Small worker client for a controller exposed behind HTTPS or localhost."""

    def __init__(self, endpoint: str, token: str, *, opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        self.endpoint = _safe_endpoint(endpoint)
        if not token.strip():
            raise ValueError("remote worker token is required")
        self.token = token
        self.opener = opener

    def run_once(self, worker_id: str) -> RemoteWorkerJob | None:
        worker = _safe_worker_id(worker_id)
        claimed = self._post("/worker/v1/claim", {"worker_id": worker})
        if claimed is None:
            return None
        dispatch = RemoteWorkerDispatch.from_dict(claimed.get("dispatch") if isinstance(claimed, dict) else None)
        if dispatch is None:
            raise ValueError("remote worker controller returned an invalid dispatch")
        evidence = execute_remote_dispatch(dispatch)
        completed = self._post("/worker/v1/complete", {
            "job_id": dispatch.job_id,
            "worker_id": worker,
            "lease_id": dispatch.lease_id,
            "lease_token": dispatch.lease_token,
            "evidence": evidence.as_dict(),
        })
        job = RemoteWorkerJob.from_dict(completed.get("job") if isinstance(completed, dict) else None)
        if job is None:
            raise ValueError("remote worker controller returned an invalid completion")
        return job

    def _post(self, path: str, body: dict[str, object]) -> dict[str, object] | None:
        request = urllib.request.Request(
            f"{self.endpoint}{path}", data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "X-Ronin-Worker-Token": self.token},
        )
        try:
            with self.opener(request, timeout=30) as response:
                if getattr(response, "status", 200) == 204:
                    return None
                raw = response.read().decode("utf-8")
        except (OSError, urllib.error.HTTPError, urllib.error.URLError, UnicodeDecodeError):
            raise ValueError("remote worker controller request failed") from None
        try:
            payload = json.loads(raw)
        except ValueError:
            raise ValueError("remote worker controller returned invalid JSON") from None
        return payload if isinstance(payload, dict) else None


def _origin_url(root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    if proc is None or proc.returncode != 0:
        raise ValueError("remote verification needs --repository-url or a credential-free HTTPS origin remote")
    return _safe_repository_url(proc.stdout)


def _run_git(
    runner: Callable[..., subprocess.CompletedProcess[str]], args: list[str], *, timeout: int,
) -> bool:
    try:
        proc = runner(["git", "-c", "core.hooksPath=/dev/null", *args], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _apply_patch(runner: Callable[..., subprocess.CompletedProcess[str]], workspace: Path, patch: str) -> bool:
    try:
        proc = runner(
            ["git", "-C", str(workspace), "apply", "--whitespace=nowarn", "-"], input=patch,
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _blocked_evidence(started: float, summary: str) -> RemoteWorkerEvidence:
    return RemoteWorkerEvidence("blocked", duration_seconds=time.monotonic() - started, summary=summary)


def _safe_job_id(value: str) -> str:
    safe = Path(value).name
    if safe != value or not _JOB_ID.fullmatch(safe):
        raise ValueError("invalid remote worker job id")
    return safe


def _valid_optional_strings(*values: object) -> bool:
    return all(value is None or isinstance(value, str) for value in values)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
