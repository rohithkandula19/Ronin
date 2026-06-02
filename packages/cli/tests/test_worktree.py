"""Tests for git worktree isolation — run against a real temporary repo, so the
actual git plumbing (add / diff / remove) is exercised, no mocking."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ronin_cli import worktree


def _init_repo(path: Path) -> None:
    def git(*args):
        subprocess.run(["git", "-C", str(path), *args], check=True,
                       capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "t@t.dev")
    git("config", "user.name", "Test")
    git("config", "commit.gpgsign", "false")
    (path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")


def test_is_git_repo(tmp_path) -> None:
    assert worktree.is_git_repo(tmp_path) is False
    _init_repo(tmp_path)
    assert worktree.is_git_repo(tmp_path) is True


def test_worktree_isolates_edits(tmp_path) -> None:
    _init_repo(tmp_path)
    main_file = tmp_path / "main.py"

    with worktree.git_worktree(tmp_path, "demo") as wt:
        assert wt.exists()
        # the worktree has its own copy of the committed file
        assert (wt / "main.py").read_text() == "print('hello')\n"
        # edit inside the worktree
        (wt / "main.py").write_text("print('changed')\n", encoding="utf-8")
        (wt / "new.py").write_text("x = 1\n", encoding="utf-8")
        # the main checkout is untouched
        assert main_file.read_text() == "print('hello')\n"

    # worktree cleaned up after the block
    assert not wt.exists()
    # main checkout still pristine
    assert main_file.read_text() == "print('hello')\n"


def test_worktree_diff_captures_edits_and_new_files(tmp_path) -> None:
    _init_repo(tmp_path)
    with worktree.git_worktree(tmp_path, "diff") as wt:
        (wt / "main.py").write_text("print('changed')\n", encoding="utf-8")
        (wt / "new.py").write_text("x = 1\n", encoding="utf-8")
        diff = worktree.worktree_diff(wt)
    assert "print('changed')" in diff
    assert "new.py" in diff          # untracked file is included
    assert "x = 1" in diff


def test_worktree_requires_git_repo(tmp_path) -> None:
    with pytest.raises(worktree.NotAGitRepo):
        with worktree.git_worktree(tmp_path, "x"):
            pass


def test_worktree_cleanup_on_error(tmp_path) -> None:
    _init_repo(tmp_path)
    captured: dict = {}
    with pytest.raises(RuntimeError):
        with worktree.git_worktree(tmp_path, "boom") as wt:
            captured["path"] = wt
            raise RuntimeError("agent blew up")
    # even though the block raised, the worktree was removed
    assert not captured["path"].exists()
