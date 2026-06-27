"""Tests for Wave 7 git snapshots (resume safety)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ronin_cli.pipeline_git_snapshot import (
    GitSnapshot,
    compare_snapshots,
    git_snapshot,
)


def _init_repo(tmp_path: Path) -> None:
    for args in (("init", "-q"), ("config", "user.email", "t@t.t"), ("config", "user.name", "t")):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True, capture_output=True)


def test_snapshot_clean_repo(tmp_path) -> None:
    _init_repo(tmp_path)
    snap = git_snapshot(tmp_path, timestamp="2026-01-01", version="0.59.0")
    assert snap.is_repo is True
    assert len(snap.git_commit_sha) == 40
    assert snap.git_branch  # e.g. main/master
    assert snap.dirty_state is False and snap.dirty_files == [] and snap.untracked_files == []
    assert snap.timestamp == "2026-01-01" and snap.ronin_version == "0.59.0"


def test_snapshot_dirty_tracked_file(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("changed\n", encoding="utf-8")
    snap = git_snapshot(tmp_path)
    assert snap.dirty_state is True
    assert "a.txt" in snap.dirty_files


def test_snapshot_untracked_file(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("x\n", encoding="utf-8")
    snap = git_snapshot(tmp_path)
    assert "new.txt" in snap.untracked_files
    assert snap.dirty_state is True


def test_snapshot_outside_repo_does_not_crash(tmp_path) -> None:
    snap = git_snapshot(tmp_path)  # plain dir, no git
    assert snap.is_repo is False
    assert snap.git_commit_sha == "" and snap.repo_path == str(tmp_path.resolve())


def test_snapshot_round_trips(tmp_path) -> None:
    _init_repo(tmp_path)
    snap = git_snapshot(tmp_path, timestamp="t", version="v")
    again = GitSnapshot.model_validate_json(snap.model_dump_json())
    assert again.git_commit_sha == snap.git_commit_sha
    assert again.is_repo is True


def test_compare_clean_when_identical(tmp_path) -> None:
    _init_repo(tmp_path)
    snap = git_snapshot(tmp_path)
    assert compare_snapshots(snap, snap).status == "clean"


def test_compare_changed_on_different_sha() -> None:
    a = GitSnapshot(repo_path="/r", is_repo=True, git_commit_sha="a" * 40, git_branch="main")
    b = GitSnapshot(repo_path="/r", is_repo=True, git_commit_sha="b" * 40, git_branch="main")
    cmp = compare_snapshots(a, b)
    assert cmp.status == "changed"
    assert any("HEAD moved" in w for w in cmp.warnings)


def test_compare_warning_on_different_path() -> None:
    a = GitSnapshot(repo_path="/r1", is_repo=True, git_commit_sha="x" * 40, git_branch="main")
    b = GitSnapshot(repo_path="/r2", is_repo=True, git_commit_sha="x" * 40, git_branch="main")
    assert compare_snapshots(a, b).status == "warning"


def test_compare_changed_on_dirty_tree() -> None:
    a = GitSnapshot(repo_path="/r", is_repo=True, git_commit_sha="x" * 40, git_branch="main")
    b = GitSnapshot(repo_path="/r", is_repo=True, git_commit_sha="x" * 40, git_branch="main",
                    dirty_files=["a.txt"], dirty_state=True)
    assert compare_snapshots(a, b).status == "changed"


def test_compare_unavailable_outside_git() -> None:
    assert compare_snapshots(None, None).status == "unavailable"
    a = GitSnapshot(is_repo=False)
    assert compare_snapshots(a, a).status == "unavailable"
