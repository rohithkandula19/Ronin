"""Disposable Git candidate workspaces with fail-closed Docker execution.

Candidate creation uses a detached Git worktree, leaving the caller's checkout
untouched. Code execution is a separate operation and deliberately has no host
fallback: a candidate must name a Docker image before it can run a command.
The container receives only the detached candidate checkout, no network by
default, dropped Linux capabilities, and no-new-privileges.
"""
from __future__ import annotations

import datetime as _datetime
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Callable, Literal

from pydantic import Field

from .mission_models import StrictModel
from .worktree import NotAGitRepo, is_git_repo, worktree_diff


CANDIDATE_SCHEMA_VERSION = 1
_CANDIDATE_ID = re.compile(r"^candidate-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
_MISSION_ID = re.compile(r"^mission-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
_GIT_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,255}$")
_WORKTREE_LOCK = threading.Lock()


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return f"candidate-{_datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


class CandidateWorkspace(StrictModel):
    id: str = Field(pattern=_CANDIDATE_ID.pattern)
    mission_id: str = Field(pattern=_MISSION_ID.pattern)
    root: str = Field(min_length=1)
    path: str = Field(min_length=1)
    base_revision: str = Field(pattern=_GIT_REVISION.pattern)
    image: str = ""
    created: str = Field(default_factory=_now)
    updated: str = Field(default_factory=_now)
    status: Literal["active", "destroyed"] = "active"
    destroyed: str | None = None


class CandidateCommandResult(StrictModel):
    command: str
    exit_code: int | None = None
    timed_out: bool = False
    blocked: bool = False
    stdout: str = ""
    stderr: str = ""
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.blocked and not self.error


class CandidateWorkspaceService:
    """Own candidate-worktree lifecycle for one repository."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.directory = self.root / ".ronin" / "candidate-workspaces"
        self._lock = threading.RLock()

    def create(
        self,
        mission_id: str,
        *,
        image: str = "",
        base_revision: str | None = None,
    ) -> CandidateWorkspace:
        """Create a detached candidate checkout; this does not run code or Docker."""
        safe_mission = _safe_mission_id(mission_id)
        if not is_git_repo(self.root):
            raise NotAGitRepo(f"{self.root} is not a Git repository")
        image = _safe_image(image) if image else ""
        revision = _git_head(self.root) if base_revision is None else base_revision.lower()
        if not _GIT_REVISION.fullmatch(revision):
            raise ValueError("candidate workspace needs a valid Git base revision")
        candidate_id = _new_id()
        parent = Path(tempfile.mkdtemp(prefix=f"ronin-candidate-{candidate_id}-"))
        workspace = parent / "workspace"
        try:
            with _WORKTREE_LOCK:
                proc = subprocess.run(
                    ["git", "-C", str(self.root), "worktree", "add", "--detach", str(workspace), revision],
                    capture_output=True, text=True, check=False,
                )
            if proc.returncode != 0:
                raise ValueError(proc.stderr.strip() or "could not create detached candidate worktree")
            candidate = CandidateWorkspace(
                id=candidate_id,
                mission_id=safe_mission,
                root=str(self.root),
                path=str(workspace),
                base_revision=revision,
                image=image,
            )
            self._save(candidate)
            return candidate
        except Exception:
            self._remove_worktree(workspace)
            shutil.rmtree(parent, ignore_errors=True)
            raise

    def list(self, *, limit: int = 100) -> tuple[CandidateWorkspace, ...]:
        try:
            paths = sorted(self.directory.glob("candidate-*.json"))
        except OSError:
            return ()
        candidates: list[CandidateWorkspace] = []
        for path in paths:
            try:
                candidates.append(self.load(path.stem))
            except ValueError:
                continue
        candidates.sort(key=lambda candidate: (candidate.updated, candidate.id), reverse=True)
        return tuple(candidates[:max(1, limit)])

    def load(self, candidate_id: str) -> CandidateWorkspace:
        safe_id = _safe_candidate_id(candidate_id)
        try:
            raw = json.loads((self.directory / f"{safe_id}.json").read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
                raise ValueError("unsupported candidate schema")
            return CandidateWorkspace.model_validate(raw.get("candidate"))
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"candidate workspace {safe_id!r} was not found or is invalid") from exc

    def diff(self, candidate_id: str) -> str:
        candidate = self._active(candidate_id)
        return worktree_diff(Path(candidate.path))

    def run_command(
        self,
        candidate_id: str,
        command: str,
        *,
        timeout: int = 600,
        allow_network: bool = False,
        run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> CandidateCommandResult:
        """Run one command in a locked-down Docker container, never the host."""
        candidate = self._active(candidate_id)
        text = command.strip()
        if not text:
            raise ValueError("candidate command must not be empty")
        if len(text) > 20_000:
            raise ValueError("candidate command exceeds the 20,000-character safety limit")
        if not 1 <= timeout <= 3_600:
            raise ValueError("candidate command timeout must be between 1 and 3600 seconds")
        if not candidate.image:
            raise ValueError("candidate execution requires a Docker image; refusing host execution")
        argv = docker_argv(candidate, text, allow_network=allow_network)
        runner = run_fn or subprocess.run
        try:
            proc = runner(argv, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return CandidateCommandResult(command=text, timed_out=True, error=f"timed out after {timeout}s")
        except OSError as exc:
            return CandidateCommandResult(command=text, blocked=True, error=f"Docker did not start: {exc}")
        return CandidateCommandResult(
            command=text,
            exit_code=proc.returncode,
            stdout=_trim(proc.stdout or ""),
            stderr=_trim(proc.stderr or ""),
        )

    def destroy(self, candidate_id: str) -> CandidateWorkspace:
        """Remove a detached candidate checkout; terminal metadata remains auditable."""
        with self._lock:
            candidate = self.load(candidate_id)
            if candidate.status == "destroyed":
                return candidate
            path = Path(candidate.path)
            self._assert_owned_candidate(candidate, path)
            _assert_candidate_path(path)
            self._remove_worktree(path)
            shutil.rmtree(path.parent, ignore_errors=True)
            destroyed = candidate.model_copy(update={
                "status": "destroyed", "destroyed": _now(), "updated": _now(),
            })
            self._save(destroyed)
            return destroyed

    def _active(self, candidate_id: str) -> CandidateWorkspace:
        candidate = self.load(candidate_id)
        if candidate.status != "active":
            raise ValueError(f"candidate workspace is {candidate.status}")
        path = Path(candidate.path)
        self._assert_owned_candidate(candidate, path)
        _assert_candidate_path(path)
        if not path.is_dir():
            raise ValueError("candidate workspace path is unavailable")
        return candidate

    def _assert_owned_candidate(self, candidate: CandidateWorkspace, path: Path) -> None:
        try:
            owner = Path(candidate.root).resolve()
            candidate_path = path.resolve()
        except OSError as exc:
            raise ValueError("candidate workspace root is invalid") from exc
        if owner != self.root:
            raise ValueError("candidate workspace belongs to a different project root")
        if candidate_path.parent.parent != Path(tempfile.gettempdir()).resolve():
            raise ValueError("candidate workspace path is outside Ronin's temporary checkout directory")

    def _remove_worktree(self, path: Path) -> None:
        if not path.exists():
            return
        with _WORKTREE_LOCK:
            subprocess.run(
                ["git", "-C", str(self.root), "worktree", "remove", "--force", str(path)],
                capture_output=True, text=True, check=False,
            )
            subprocess.run(
                ["git", "-C", str(self.root), "worktree", "prune"],
                capture_output=True, text=True, check=False,
            )

    def _save(self, candidate: CandidateWorkspace) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{candidate.id}.json"
        payload = {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "candidate": candidate.model_dump(mode="json"),
        }
        _atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def docker_argv(candidate: CandidateWorkspace, command: str, *, allow_network: bool = False) -> list[str]:
    """Build a fixed, no-host-fallback Docker argv for candidate verification."""
    image = _safe_image(candidate.image)
    workspace = Path(candidate.path).resolve()
    _assert_candidate_path(workspace)
    argv = [
        "docker", "run", "--rm", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "256",
        "--memory", "2g",
    ]
    if not allow_network:
        argv.extend(["--network", "none"])
    argv.extend([
        "--mount", f"type=bind,src={workspace},dst=/workspace,rw",
        "--workdir", "/workspace", image, "sh", "-lc", command,
    ])
    return argv


def _git_head(root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        raise ValueError(f"could not resolve candidate base revision: {exc}") from exc
    revision = proc.stdout.strip().lower()
    if proc.returncode != 0 or not _GIT_REVISION.fullmatch(revision):
        raise ValueError("candidate workspace needs a Git repository with an initial commit")
    return revision


def _safe_candidate_id(value: str) -> str:
    safe = Path(value).name
    if safe != value or not _CANDIDATE_ID.fullmatch(safe):
        raise ValueError("invalid candidate workspace id")
    return safe


def _safe_mission_id(value: str) -> str:
    safe = Path(value).name
    if safe != value or not _MISSION_ID.fullmatch(safe):
        raise ValueError("invalid mission id")
    return safe


def _safe_image(value: str) -> str:
    if not _IMAGE.fullmatch(value) or value.startswith("-"):
        raise ValueError("invalid Docker image reference")
    return value


def _assert_candidate_path(path: Path) -> None:
    resolved = path.resolve()
    if (
        resolved.name != "workspace"
        or not resolved.parent.name.startswith("ronin-candidate-candidate-")
        or resolved.parent.parent != Path(tempfile.gettempdir()).resolve()
    ):
        raise ValueError("candidate workspace path is unsafe")


def _trim(value: str, limit: int = 12_000) -> str:
    return value if len(value) <= limit else value[:limit] + "\n[truncated]"


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
