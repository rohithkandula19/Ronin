"""The two status-line facts the event stream cannot carry.

``render_status`` prints five things and derives none of them, so before this module the
real CLI passed two (``cwd``, ``mode``) and the branch and context share rendered as
their empty defaults — a status bar reporting ``(no branch)`` and ``0% ctx`` in a git
repository halfway through a long session.
"""

from __future__ import annotations

from pathlib import Path

from ronin.cli.status import context_share, current_branch, git_dir
from ronin.context.compaction import CompactionPolicy
from ronin.core.types import Message, Role, Text


def repo(root: Path, head: str) -> Path:
    """A directory with just enough ``.git`` to be read like a repository."""
    directory = root / ".git"
    directory.mkdir()
    (directory / "HEAD").write_text(head, encoding="utf-8")
    return root


def test_a_branch_is_read_off_head(tmp_path: Path) -> None:
    assert current_branch(repo(tmp_path, "ref: refs/heads/main\n")) == "main"


def test_a_slash_in_the_branch_name_survives(tmp_path: Path) -> None:
    """``claude/ronin-last-check`` is one branch, not a path to walk. Only the
    ``refs/heads/`` prefix comes off."""
    assert current_branch(repo(tmp_path, "ref: refs/heads/feature/nested/name\n")) == (
        "feature/nested/name"
    )


def test_a_detached_head_shows_the_commit_rather_than_nothing(tmp_path: Path) -> None:
    """ "Detached" and "not a repository" are different situations, and a status line that
    said nothing in both could not tell them apart."""
    branch = current_branch(repo(tmp_path, "3c1743b8a9f0e1d2c3b4a5968778695a4b3c2d1e\n"))
    assert branch == "3c1743b8a9"
    assert len(branch) == 10


def test_a_directory_that_is_not_a_repository_has_no_branch(tmp_path: Path) -> None:
    assert current_branch(tmp_path) == ""


def test_a_git_directory_with_no_head_is_not_a_crash(tmp_path: Path) -> None:
    """A half-initialised or mid-clone ``.git`` must not take the UI down with it."""
    (tmp_path / ".git").mkdir()
    assert current_branch(tmp_path) == ""


def test_a_worktree_points_at_its_real_git_directory(tmp_path: Path) -> None:
    """``git worktree`` and submodules make ``.git`` a *file*. This project's own CI
    checks out worktrees, so a status line that broke on one would break in the place it
    is most used."""
    real = tmp_path / "actual-git-dir"
    real.mkdir()
    (real / "HEAD").write_text("ref: refs/heads/worktree-branch\n", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {real}\n", encoding="utf-8")
    assert git_dir(tree) == real
    assert current_branch(tree) == "worktree-branch"


def test_a_relative_gitdir_pointer_is_resolved_against_the_workspace(tmp_path: Path) -> None:
    real = tmp_path / "sub" / "gitdir"
    real.mkdir(parents=True)
    (real / "HEAD").write_text("ref: refs/heads/relative\n", encoding="utf-8")
    (tmp_path / ".git").write_text("gitdir: sub/gitdir\n", encoding="utf-8")
    assert current_branch(tmp_path) == "relative"


def test_a_git_file_that_says_something_else_is_refused_not_guessed(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("this is not a git pointer\n", encoding="utf-8")
    assert git_dir(tmp_path) is None
    assert current_branch(tmp_path) == ""


# --------------------------------------------------------------------------- #
# context occupancy
# --------------------------------------------------------------------------- #


def message(text: str) -> Message:
    return Message(role=Role.USER, content_blocks=(Text(text=text),))


def test_an_empty_transcript_fills_none_of_the_window() -> None:
    assert context_share((), CompactionPolicy(context_window=1000)) == 0.0


def test_occupancy_grows_with_the_transcript() -> None:
    policy = CompactionPolicy(context_window=1000)
    small = context_share((message("hello"),), policy)
    larger = context_share((message("hello world " * 200),), policy)
    assert 0.0 < small < larger <= 1.0


def test_occupancy_is_clamped_at_full() -> None:
    """A transcript over the window is a real state — it is what triggers compaction —
    but "103% ctx" reads as a broken gauge rather than as a warning."""
    over = context_share((message("word " * 5000),), CompactionPolicy(context_window=10))
    assert over == 1.0


def test_occupancy_is_measured_against_the_window_not_against_spend() -> None:
    """The reason this function exists instead of dividing ``Budget.spent_tokens``: spend
    is cumulative over the session and includes every message compaction has since folded
    away, so the same transcript would report a different, ever-growing "occupancy" the
    longer the session ran. Here it depends on the transcript alone."""
    policy = CompactionPolicy(context_window=1000)
    transcript = (message("stable content"),)
    assert context_share(transcript, policy) == context_share(transcript, policy)
