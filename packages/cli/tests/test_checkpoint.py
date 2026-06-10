"""Tests for workspace checkpoint & rewind — real temp git repos."""
from __future__ import annotations

import subprocess

import pytest

from ronin_cli import checkpoint
from ronin_cli.worktree import NotAGitRepo


def _init_repo(path) -> None:
    def git(*a):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "t@t.dev")
    git("config", "user.name", "Test")
    git("config", "commit.gpgsign", "false")
    (path / "main.py").write_text("print('v1')\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")


def test_checkpoint_and_rewind_restores_and_removes(tmp_path) -> None:
    _init_repo(tmp_path)
    cp = checkpoint.create_checkpoint(tmp_path, "before changes", now=1000.0)
    assert cp.id == 1

    # mutate: edit a tracked file, add a new untracked file
    (tmp_path / "main.py").write_text("print('v2-broken')\n", encoding="utf-8")
    (tmp_path / "newfile.py").write_text("oops = 1\n", encoding="utf-8")

    msg = checkpoint.restore_checkpoint(tmp_path, 1)
    assert "rewound to checkpoint #1" in msg
    # tracked file restored to snapshot
    assert (tmp_path / "main.py").read_text() == "print('v1')\n"
    # file created after the checkpoint is removed (true rewind)
    assert not (tmp_path / "newfile.py").exists()


def test_checkpoint_captures_untracked_then_restores_it(tmp_path) -> None:
    _init_repo(tmp_path)
    # an untracked file present at checkpoint time...
    (tmp_path / "draft.py").write_text("keep = 1\n", encoding="utf-8")
    checkpoint.create_checkpoint(tmp_path, "with draft", now=1.0)
    # ...is deleted, then rewind brings it back
    (tmp_path / "draft.py").unlink()
    checkpoint.restore_checkpoint(tmp_path, 1)
    assert (tmp_path / "draft.py").read_text() == "keep = 1\n"


def test_list_checkpoints(tmp_path) -> None:
    _init_repo(tmp_path)
    checkpoint.create_checkpoint(tmp_path, "first", now=1.0)
    checkpoint.create_checkpoint(tmp_path, "second", now=2.0)
    cps = checkpoint.list_checkpoints(tmp_path)
    assert [c.id for c in cps] == [1, 2]
    assert [c.label for c in cps] == ["first", "second"]


def test_checkpoint_requires_git(tmp_path) -> None:
    with pytest.raises(NotAGitRepo):
        checkpoint.create_checkpoint(tmp_path, "x")


def test_rewind_unknown_id(tmp_path) -> None:
    _init_repo(tmp_path)
    assert "no checkpoint #99" in checkpoint.restore_checkpoint(tmp_path, 99)


def test_checkpoint_tools_and_sensitivity(tmp_path) -> None:
    from ronin_cli.code_tools import SENSITIVE_TOOLS
    tools = {t.name: t for t in checkpoint.build_checkpoint_tools(tmp_path)}
    assert set(tools) == {"checkpoint", "list_checkpoints", "rewind"}
    assert "rewind" in SENSITIVE_TOOLS  # destructive → gated

    _init_repo(tmp_path)
    out = tools["checkpoint"].handler(label="t1")
    assert "created checkpoint #1" in out
    assert "#1 t1" in tools["list_checkpoints"].handler()


def test_checkpoint_tool_degrades_without_git(tmp_path) -> None:
    tools = {t.name: t for t in checkpoint.build_checkpoint_tools(tmp_path)}
    assert "cannot checkpoint" in tools["checkpoint"].handler(label="x")
