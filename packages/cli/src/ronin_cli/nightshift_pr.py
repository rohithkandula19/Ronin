"""Turn a Nightshift patch into a real GitHub PR — safely.

Each patch becomes its own branch (created in an isolated worktree, so the
user's checkout is never touched), the patch is applied + committed there, the
branch is pushed, and a PR is opened via ``gh``. Branch naming is pure and
unit-tested; the branch+apply+commit step is testable against a temp repo; push
and PR-creation are thin wrappers.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


def branch_name(title: str, index: int) -> str:
    """A safe git branch name for a task, e.g. 'ronin/03-fix-the-auth-bug'. Pure."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    return f"ronin/{index:02d}-{slug or 'task'}"


def _git(root: Path, *args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, timeout=kw.get("timeout", 60))


def branch_commit_in_worktree(root: Path | str, patch_file: str, branch: str,
                              message: str) -> tuple[bool, str]:
    """In a fresh worktree on ``branch``, apply ``patch_file`` and commit. Returns
    (ok, note). The worktree is torn down but the branch is kept. Never touches
    the main checkout."""
    from .worktree import git_worktree_branch
    patch = Path(patch_file)
    if not patch.is_file():
        return False, "patch file missing"
    try:
        with git_worktree_branch(root, branch) as wt:
            ap = subprocess.run(["git", "-C", str(wt), "apply", "--whitespace=nowarn",
                                 str(patch.resolve())], capture_output=True, text=True, timeout=30)
            if ap.returncode != 0:
                return False, "patch did not apply cleanly"
            _git(wt, "add", "-A")
            cm = _git(wt, "commit", "-m", message)
            if cm.returncode != 0:
                return False, "nothing to commit"
        return True, "committed on " + branch
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:120]


def push_branch(root: Path | str, branch: str) -> bool:
    r = _git(Path(root), "push", "-u", "origin", branch, timeout=90)
    return r.returncode == 0


def open_pr(root: Path | str, branch: str, title: str, body: str) -> str | None:
    """Open a PR for ``branch`` via gh; returns the PR URL or None."""
    import shutil
    if not shutil.which("gh"):
        return None
    r = subprocess.run(["gh", "pr", "create", "--head", branch, "--title", title,
                        "--body", body], cwd=str(Path(root).resolve()),
                       capture_output=True, text=True, timeout=90)
    return r.stdout.strip() if r.returncode == 0 else None
