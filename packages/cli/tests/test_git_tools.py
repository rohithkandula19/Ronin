"""Tests for the read-only git tools (git_status / git_diff / git_log)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ronin_cli.git_tools import build_git_tools, parse_blame


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


# ---- git_blame ------------------------------------------------------------

_PORCELAIN = """abc1234def567890123456789012345678901234 1 1 2
author Alice
author-mail <alice@example.com>
author-time 1700000000
author-tz +0000
committer Alice
summary add greeting
filename hello.py
\tprint("hi")
abc1234def567890123456789012345678901234 2 2
\tprint("bye")
"""


def test_parse_blame_caches_commit_metadata() -> None:
    lines = parse_blame(_PORCELAIN)
    assert len(lines) == 2
    # both lines share the same commit; metadata is only printed once but reused
    assert all(b.commit == "abc1234d" for b in lines)
    assert all(b.author == "Alice" and b.summary == "add greeting" for b in lines)
    assert lines[0].line == 1 and lines[1].line == 2
    assert lines[0].date == "2023-11-14"   # 1700000000 epoch (UTC)


def test_parse_blame_empty() -> None:
    assert parse_blame("") == []


def test_git_blame_outside_repo(tmp_path: Path) -> None:
    t = _tools(tmp_path)
    assert "not a git repository" in t["git_blame"]("a.txt")


def test_git_blame_in_repo(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "Blamer")
    (tmp_path / "f.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "seed the file")
    t = _tools(tmp_path)

    out = t["git_blame"]("f.py", line_start=1, line_end=3)
    assert "Blamer" in out and "seed the file" in out
    assert "1:" in out and "3:" in out

    # default line_end == line_start (single line)
    one = t["git_blame"]("f.py", line_start=2)
    assert one.count("\n") == 0 and "2:" in one

    # missing path / bad file -> clean error string, no raise
    assert "ERROR" in t["git_blame"]("")
    assert "ERROR" in t["git_blame"]("does_not_exist.py")
