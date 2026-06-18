"""Helpers for `ronin version` git context and the `ronin update` command.

Everything here is offline and pure stdlib. The git calls are best-effort:
a missing git binary, a non-checkout install (pip/uv wheel), or a detached
state must never raise out of these helpers. Callers get None and print the
plain version line instead.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from . import __version__


def find_repo_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from ``start`` (default: this file) to the dir containing .git.

    Returns the repo root Path, or None when the install is not a git checkout
    (e.g. installed from a wheel). Never raises.
    """
    here = (start or Path(__file__)).resolve()
    # Path.parents does not include the path itself; check the dir first.
    candidates = [here] if here.is_dir() else []
    candidates += list(here.parents)
    for d in candidates:
        if (d / ".git").exists():
            return d
    return None


def _git(root: Path, args: list[str], timeout: int = 15) -> Optional[str]:
    """Run ``git -C root <args>`` and return stripped stdout, or None on any error."""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def git_short_sha(root: Path) -> Optional[str]:
    """Short commit sha for the checkout at ``root``, or None. Never raises."""
    return _git(root, ["rev-parse", "--short", "HEAD"]) or None


def git_branch(root: Path) -> Optional[str]:
    """Current branch name for the checkout at ``root``, or None. Never raises."""
    return _git(root, ["rev-parse", "--abbrev-ref", "HEAD"]) or None


def version_line() -> str:
    """The string printed by `ronin version`.

    "ronin X.Y.Z" for a non-checkout install, or
    "ronin X.Y.Z (sha, branch)" for a git checkout.
    """
    base = f"ronin {__version__}"
    root = find_repo_root()
    if root is None:
        return base
    sha = git_short_sha(root)
    branch = git_branch(root)
    if sha and branch:
        return f"{base} ({sha}, {branch})"
    if sha:
        return f"{base} ({sha})"
    return base
