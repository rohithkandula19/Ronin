"""Read-only unified-diff capture for evidence-based pipeline verification.

Captures the working tree's actual diff (tracked changes vs HEAD: staged +
unstaged) so the verifier and semantic-contract stages can reason about real
evidence instead of the implementer's self-reported ``diff_summary``. Pure I/O:
it only runs ``git diff`` — it never mutates files, never needs the network, and
never raises (it fails closed to ``captured=False``). A large diff is truncated
to a byte budget with an explicit ``truncated`` flag.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class DiffEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    captured: bool = False          # did we obtain a diff at all?
    files_changed: list[str] = Field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    truncated: bool = False
    diff_excerpt: str = ""
    full_diff_available: bool = False  # a non-empty diff existed (excerpt may be trimmed)
    note: str = ""                  # honest explanation when nothing was captured


def _git(root, *args, timeout: int = 20) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def capture_diff(root, *, context: int = 3, max_bytes: int = 20000,
                 include: bool = True) -> DiffEvidence:
    """Capture the working-tree diff vs HEAD (read-only). Never raises.

    ``include=False`` → ``--no-diff-evidence`` (captured=False). Outside a repo or
    with no changes, ``captured`` is still set honestly with a ``note``."""
    if not include:
        return DiffEvidence(captured=False, note="diff evidence disabled")

    root = str(Path(root).resolve())
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.returncode != 0:
        return DiffEvidence(captured=False, note="not a git repository")

    numstat = _git(root, "diff", "--numstat", "HEAD")
    files: list[str] = []
    adds = dels = 0
    if numstat is not None and numstat.returncode == 0:
        for line in numstat.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            a, d, path = parts
            files.append(path.strip())
            if a.isdigit():
                adds += int(a)
            if d.isdigit():
                dels += int(d)

    full = _git(root, "diff", f"-U{max(0, context)}", "HEAD")
    diff_text = full.stdout if (full is not None and full.returncode == 0) else ""

    if not diff_text.strip():
        return DiffEvidence(captured=True, files_changed=files, additions=adds,
                            deletions=dels, full_diff_available=False,
                            note="no tracked changes in the working tree")

    truncated = len(diff_text) > max_bytes
    excerpt = diff_text[:max_bytes] + ("\n… [diff truncated]" if truncated else "") if max_bytes >= 0 else diff_text
    return DiffEvidence(
        captured=True, files_changed=files, additions=adds, deletions=dels,
        truncated=truncated, diff_excerpt=excerpt, full_diff_available=True,
        note=("diff truncated to the byte budget" if truncated else ""),
    )
