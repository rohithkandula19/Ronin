"""Tests for Nightshift PR-opening — branch naming + worktree branch-commit."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ronin_cli.nightshift_pr import branch_commit_in_worktree, branch_name


def test_branch_name_slugifies() -> None:
    assert branch_name("Fix the Auth Bug!", 3) == "ronin/03-fix-the-auth-bug"
    assert branch_name("", 1) == "ronin/01-task"
    assert branch_name("a" * 100, 5).startswith("ronin/05-")


def test_branch_name_strips_weird_chars() -> None:
    b = branch_name("feat: add /retry & backoff", 2)
    assert b.startswith("ronin/02-") and "/" not in b[len("ronin/"):]
    assert ":" not in b and "&" not in b


def _git(root: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, check=True)


def test_branch_commit_in_worktree(tmp_path: Path) -> None:
    # real repo with a committed file
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "x.txt").write_text("hello\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    # produce a patch (edit then capture diff then revert)
    (tmp_path / "x.txt").write_text("hello world\n", encoding="utf-8")
    diff = subprocess.run(["git", "-C", str(tmp_path), "diff"], capture_output=True, text=True).stdout
    _git(tmp_path, "checkout", "x.txt")
    patch = tmp_path / "p.patch"
    patch.write_text(diff, encoding="utf-8")

    ok, note = branch_commit_in_worktree(tmp_path, str(patch), "ronin/01-test", "apply test")
    assert ok, note
    # the branch exists and carries the change; the main checkout is untouched
    branches = subprocess.run(["git", "-C", str(tmp_path), "branch"], capture_output=True, text=True).stdout
    assert "ronin/01-test" in branches
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "hello\n"   # main tree unchanged
    show = subprocess.run(["git", "-C", str(tmp_path), "show", "ronin/01-test:x.txt"],
                          capture_output=True, text=True).stdout
    assert show == "hello world\n"   # branch has the patched content


def test_branch_commit_missing_patch(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    ok, note = branch_commit_in_worktree(tmp_path, "/nope.patch", "ronin/01-x", "m")
    assert not ok and "missing" in note
