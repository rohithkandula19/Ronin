"""`make_local_git_evaluator` runs `git reset --hard` + `git clean -fdx` before
every task. Its docstring warned "point it at a disposable clone, never a working
tree with changes you want" — but a warning is not a guard. Nothing stopped you
aiming it at the repo you actually work in and losing everything in it.

It is now enforced: a dirty tree raises instead of being destroyed. These tests
create REAL git repos and assert the files survive.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ronin_eval_suite.swebench import DirtyWorkingTreeError, make_local_git_evaluator


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    (root / "committed.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def test_clean_tree_is_allowed(repo: Path):
    # nothing to lose -> the destructive reset destroys nothing -> proceed
    ev = make_local_git_evaluator(repo)
    assert ev is not None


def test_uncommitted_change_is_refused_and_survives(repo: Path):
    (repo / "committed.py").write_text("x = 2   # my unsaved work\n")

    with pytest.raises(DirtyWorkingTreeError) as e:
        make_local_git_evaluator(repo)

    assert "uncommitted" in str(e.value)
    # the whole point: the work is still there
    assert "my unsaved work" in (repo / "committed.py").read_text()


def test_untracked_file_is_refused_and_survives(repo: Path):
    (repo / "notes.md").write_text("three weeks of notes")

    with pytest.raises(DirtyWorkingTreeError):
        make_local_git_evaluator(repo)

    assert (repo / "notes.md").read_text() == "three weeks of notes"


def test_error_names_what_would_be_destroyed(repo: Path):
    (repo / "a.txt").write_text("a")
    (repo / "b.txt").write_text("b")
    with pytest.raises(DirtyWorkingTreeError) as e:
        make_local_git_evaluator(repo)
    msg = str(e.value)
    assert "a.txt" in msg and "b.txt" in msg
    assert "disposable clone" in msg


def test_allow_dirty_is_an_explicit_override(repo: Path):
    (repo / "scratch.txt").write_text("throwaway")
    ev = make_local_git_evaluator(repo, allow_dirty=True)   # deliberate
    assert ev is not None


def test_non_git_directory_is_refused(tmp_path: Path):
    plain = tmp_path / "not_a_repo"
    plain.mkdir()
    (plain / "important.txt").write_text("do not delete me")

    with pytest.raises(DirtyWorkingTreeError) as e:
        make_local_git_evaluator(plain)

    assert "not a git repository" in str(e.value)
    assert (plain / "important.txt").exists()
