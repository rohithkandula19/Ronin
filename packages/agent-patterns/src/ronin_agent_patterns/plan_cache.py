"""Persistent, repository-scoped cache for structured planner output.

The cache is deliberately small and local: entries live below
``<repo>/.ronin/plans/`` and are keyed by a normalized task digest plus a live
repository fingerprint. A changed working tree therefore selects a different
entry instead of reusing a plan made for older code.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

from pydantic import BaseModel, ValidationError

_CACHE_VERSION = 1
_CACHE_DIR = Path(".ronin") / "plans"
_SKIP_DIRS = {".git", ".ronin", ".venv", "node_modules", "build", "dist", "__pycache__"}
_MAX_FINGERPRINT_FILES = 5_000
_MAX_FINGERPRINT_BYTES_PER_FILE = 128 * 1024


class Plan(BaseModel):
    """Structured output shared by the planner, executor, and local cache."""

    goal: str
    steps: list[str]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def task_digest(task: str) -> str:
    """Return a stable digest for semantically unchanged task whitespace."""
    return _digest(" ".join(task.split()))


def _git_fingerprint(root: Path) -> str | None:
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=2,
        )
        if head.returncode:
            return None
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True, text=True, check=False, timeout=2,
        )
        if status.returncode:
            return None
    except (OSError, subprocess.SubprocessError):
        return None

    # The cache itself is untracked by design, so its own writes must not make
    # every later lookup appear stale. The fingerprint stores only a digest.
    changed = []
    for line in status.stdout.splitlines():
        path = line[3:]
        if not path.startswith(".ronin/"):
            changed.append(line)
    return _digest("git-v1\0" + head.stdout.strip() + "\0" + "\n".join(changed))


def _filesystem_fingerprint(root: Path) -> str:
    """Fallback for non-git directories; bounded and deliberately local-only."""
    digest = hashlib.sha256(b"filesystem-v1\0")
    try:
        files = sorted(
            path for path in root.rglob("*")
            if path.is_file() and not any(part in _SKIP_DIRS for part in path.relative_to(root).parts)
        )
    except OSError:
        files = []

    for path in files[:_MAX_FINGERPRINT_FILES]:
        try:
            stat = path.stat()
            relative = path.relative_to(root).as_posix()
            digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\0".encode("utf-8"))
            with path.open("rb") as handle:
                digest.update(handle.read(_MAX_FINGERPRINT_BYTES_PER_FILE))
        except OSError:
            continue
    digest.update(str(len(files)).encode("ascii"))
    return digest.hexdigest()


def repository_fingerprint(root: Path | str) -> str:
    """Fingerprint the current repository state without storing source text."""
    resolved = Path(root).resolve()
    return _git_fingerprint(resolved) or _filesystem_fingerprint(resolved)


class PlanCache:
    """Atomic local cache for initial planner output.

    Replans after an execution failure intentionally bypass the cache: their
    failure context is new input and must be sent to the planner.
    """

    def __init__(self, root: Path | str, *, max_entries: int = 128) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self.root = Path(root).resolve()
        self.max_entries = max_entries

    @property
    def directory(self) -> Path:
        return self.root / _CACHE_DIR

    def _cache_path(self, task: str, fingerprint: str) -> Path:
        key = _digest(f"{fingerprint}\0{task_digest(task)}")
        return self.directory / f"{key}.json"

    def cache_path(self, task: str) -> Path:
        return self._cache_path(task, repository_fingerprint(self.root))

    def load(self, task: str) -> Plan | None:
        fingerprint = repository_fingerprint(self.root)
        path = self._cache_path(task, fingerprint)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if (raw.get("version") != _CACHE_VERSION
                    or raw.get("task_digest") != task_digest(task)
                    or raw.get("repository_fingerprint") != fingerprint):
                return None
            return Plan.model_validate(raw.get("plan"))
        except (OSError, ValueError, TypeError, ValidationError):
            return None

    def store(self, task: str, plan: Plan) -> bool:
        """Persist one plan atomically. Cache write failures never fail a run."""
        fingerprint = repository_fingerprint(self.root)
        path = self._cache_path(task, fingerprint)
        payload = {
            "version": _CACHE_VERSION,
            "task_digest": task_digest(task),
            "repository_fingerprint": fingerprint,
            "plan": plan.model_dump(),
        }
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            os.replace(temporary, path)
            self._prune()
            return True
        except OSError:
            return False
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _prune(self) -> None:
        try:
            entries = sorted(
                self.directory.glob("*.json"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
            for path in entries[self.max_entries:]:
                path.unlink(missing_ok=True)
        except OSError:
            pass
