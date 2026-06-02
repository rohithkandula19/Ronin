"""Tests for the read-only git tools (git_status / git_diff / git_log)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ro_claude_kit_cli.git_tools import build_git_tools


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


def _tools(root: Path) -> dict:
    return {t.name: t.handler for t in build_git_tools(root)}


def test_git_tools_outside_repo(tmp_path: Path) -> None:
    t = _tools(tmp_path)
    assert "not a git repository" in t["git_status"]()
    assert "not a git repository" in t["git_diff"]()
    assert "not a git repository" in t["git_log"]()


def test_git_status_diff_log_in_repo(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init: add a.txt")
    t = _tools(tmp_path)

    # clean tree
    assert "working tree clean" in t["git_status"]()
    # log shows the commit
    assert "init: add a.txt" in t["git_log"]()

    # now dirty it
    (tmp_path / "a.txt").write_text("hello world\n", encoding="utf-8")
    status = t["git_status"]()
    assert "a.txt" in status and "clean" not in status
    diff = t["git_diff"]()
    assert "hello world" in diff and "+hello world" in diff

    # staged diff is empty until we stage
    assert "no staged changes" in t["git_diff"](staged=True)
    _git(tmp_path, "add", "a.txt")
    assert "hello world" in t["git_diff"](staged=True)


def test_git_diff_path_scoping(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    (tmp_path / "a.txt").write_text("a2\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b2\n", encoding="utf-8")
    t = _tools(tmp_path)
    diff_a = t["git_diff"](path="a.txt")
    assert "a2" in diff_a and "b2" not in diff_a
