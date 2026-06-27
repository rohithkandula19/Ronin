"""Git / working-tree snapshots for safe pipeline resume.

A :class:`GitSnapshot` records the repo's HEAD, branch, and dirty/untracked
files at save time so ``--resume`` can detect when the tree moved underneath a
checkpoint. Pure and serializable; ``git_snapshot`` never raises (it fails
closed to ``is_repo=False``), and nothing here ever mutates the working tree.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class GitSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo_path: str = ""
    is_repo: bool = False
    git_commit_sha: str = ""
    git_branch: str = ""
    dirty_state: bool = False
    dirty_files: list[str] = Field(default_factory=list)      # modified/staged tracked
    untracked_files: list[str] = Field(default_factory=list)
    timestamp: str = ""
    ronin_version: str = ""


def _run_git(root, *args, timeout: int = 10) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def git_snapshot(root, *, timestamp: str = "", version: str = "") -> GitSnapshot:
    """Capture the repo state at ``root``. Never raises; fails closed outside git."""
    repo_path = str(Path(root).resolve())
    snap = GitSnapshot(repo_path=repo_path, timestamp=timestamp, ronin_version=version)

    inside = _run_git(repo_path, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.returncode != 0:
        return snap  # not a repo (is_repo stays False)
    snap.is_repo = True

    head = _run_git(repo_path, "rev-parse", "HEAD")
    if head is not None and head.returncode == 0:
        snap.git_commit_sha = head.stdout.strip()
    branch = _run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    if branch is not None and branch.returncode == 0:
        snap.git_branch = branch.stdout.strip()

    status = _run_git(repo_path, "status", "--porcelain")
    if status is not None and status.returncode == 0:
        for line in status.stdout.splitlines():
            if not line.strip():
                continue
            if line.startswith("??"):
                snap.untracked_files.append(line[3:].strip())
            else:
                snap.dirty_files.append(line[3:].strip())
    snap.dirty_state = bool(snap.dirty_files or snap.untracked_files)
    return snap


class SnapshotComparison(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str = "unavailable"  # clean / changed / warning / unavailable
    warnings: list[str] = Field(default_factory=list)


def compare_snapshots(saved: GitSnapshot | None,
                      current: GitSnapshot | None) -> SnapshotComparison:
    """Compare a saved snapshot against the current repo state (pure).

    - no git on either side → ``unavailable``
    - HEAD moved or the dirty/untracked file sets changed → ``changed``
    - repo path or branch differs → ``warning``
    - nothing differs → ``clean``

    'changed' outranks 'warning'; both are non-clean and should make a resume ask
    for confirmation (or ``--force-resume``)."""
    cmp = SnapshotComparison()
    if saved is None or current is None or not saved.is_repo or not current.is_repo:
        cmp.status = "unavailable"
        cmp.warnings.append("git state unavailable — can't verify the tree is unchanged")
        return cmp

    status = "clean"
    if saved.repo_path != current.repo_path:
        status = "warning"
        cmp.warnings.append(
            f"repo path differs: saved {saved.repo_path}, now {current.repo_path}")
    if saved.git_commit_sha != current.git_commit_sha:
        status = "changed"
        cmp.warnings.append(
            f"HEAD moved from {saved.git_commit_sha[:8] or '(none)'} "
            f"to {current.git_commit_sha[:8] or '(none)'}")
    if saved.git_branch != current.git_branch:
        if status != "changed":
            status = "warning"
        cmp.warnings.append(f"branch differs: saved {saved.git_branch}, now {current.git_branch}")
    if (set(saved.dirty_files) != set(current.dirty_files)
            or set(saved.untracked_files) != set(current.untracked_files)):
        if status != "changed":
            status = "changed"
        cmp.warnings.append("working tree changed since the checkpoint")

    cmp.status = status
    return cmp
