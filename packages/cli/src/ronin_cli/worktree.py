"""Git worktree isolation for parallel mutating sub-agents.

When several sub-agents edit the same repo at once, their writes collide. A git
*worktree* gives each one its own checkout of the same repository — separate
working directory, shared object store — so N agents can mutate in parallel
without stepping on each other. Each agent's changes become a self-contained
diff the parent (and the user, via an approval gate) can review and apply.

This module is pure git plumbing: create a worktree, capture its diff vs HEAD
(including new untracked files), and tear it down. It's fully testable against a
real temporary repo — no network, no LLM.
"""
from __future__ import annotations

import subprocess
import tempfile
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator, Mapping, Sequence

# Serializes the `git worktree add/remove` plumbing. The administrative area
# (.git/worktrees) isn't safe to mutate from several threads at once, so we hold
# this only around the git calls — the agent work in between runs lock-free, so
# parallelism is preserved where it matters.
_WT_LOCK = threading.Lock()


class NotAGitRepo(Exception):
    """The root isn't inside a git work tree, so worktrees aren't available."""


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=check,
    )


def is_git_repo(root: Path | str) -> bool:
    try:
        out = _git(Path(root), "rev-parse", "--is-inside-work-tree", check=False)
    except (OSError, FileNotFoundError):
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


def worktree_diff(worktree: Path) -> str:
    """All changes in ``worktree`` relative to HEAD, including new untracked
    files. We stage everything (``add -A``) into the worktree's own index, then
    diff the index against HEAD — this is the one diff that captures creates,
    edits, and deletes together. Staging touches only this worktree's index."""
    _git(worktree, "add", "-A", check=False)
    out = _git(worktree, "diff", "--cached", "HEAD", check=False)
    return out.stdout


@contextmanager
def git_worktree_branch(root: Path | str, branch: str, label: str = "pr") -> Iterator[Path]:
    """Create a worktree on a NEW branch ``branch`` (off HEAD) and clean up the
    worktree on exit — but keep the branch (so it can be pushed). Use for turning
    an autonomous patch into a real PR branch without touching the main checkout.
    """
    root_path = Path(root).resolve()
    if not is_git_repo(root_path):
        raise NotAGitRepo(f"{root_path} is not a git repository — worktree isolation needs git")

    tmp = Path(tempfile.mkdtemp(prefix=f"ronin-wt-{label}-"))
    target = tmp / "wt"
    try:
        with _WT_LOCK:
            _git(root_path, "worktree", "add", "-b", branch, str(target), "HEAD")
        yield target
    finally:
        with _WT_LOCK:
            _git(root_path, "worktree", "remove", "--force", str(target), check=False)
        try:
            target.rmdir()
        except OSError:
            pass
        try:
            tmp.rmdir()
        except OSError:
            pass
        _git(root_path, "worktree", "prune", check=False)


@contextmanager
def git_worktree(root: Path | str, label: str = "agent") -> Iterator[Path]:
    """Create a detached worktree at HEAD and clean it up on exit.

    Yields the path to the isolated checkout. The worktree is always removed
    (``--force``) when the block exits, even on error, so no stray checkouts
    accumulate. Detached HEAD means we don't create or leak branches.
    """
    root_path = Path(root).resolve()
    if not is_git_repo(root_path):
        raise NotAGitRepo(f"{root_path} is not a git repository — worktree isolation needs git")

    tmp = Path(tempfile.mkdtemp(prefix=f"ronin-wt-{label}-"))
    # mkdtemp made the dir; `git worktree add` insists on creating it itself.
    target = tmp / "wt"
    try:
        with _WT_LOCK:
            _git(root_path, "worktree", "add", "--detach", str(target), "HEAD")
        yield target
    finally:
        with _WT_LOCK:
            _git(root_path, "worktree", "remove", "--force", str(target), check=False)
        # best-effort cleanup of the temp parent + prune bookkeeping
        try:
            target.rmdir()
        except OSError:
            pass
        try:
            tmp.rmdir()
        except OSError:
            pass
        _git(root_path, "worktree", "prune", check=False)


@contextmanager
def git_worktree_pool(
    root: Path | str,
    labels: Sequence[str],
) -> Iterator[Mapping[str, Path]]:
    """Lease one detached worktree per unique role label.

    A single throwaway worktree protects the caller's checkout, but concurrent
    mutating agents would still overwrite each other inside it. This pool gives
    every write-capable role a separate detached checkout while leaving Git's
    administrative updates serialized by :func:`git_worktree`.

    The mapping keys are the supplied role labels. Duplicate or blank labels are
    rejected before any worktree is created, so a routing bug cannot silently
    make two agents share a filesystem.
    """
    normalized = [label.strip() for label in labels]
    if any(not label for label in normalized):
        raise ValueError("worktree pool labels must be non-empty")
    if len(set(normalized)) != len(normalized):
        raise ValueError("worktree pool labels must be unique")

    with ExitStack() as stack:
        leases = {
            label: stack.enter_context(git_worktree(root, label=f"agent-{label}"))
            for label in normalized
        }
        yield leases
