"""Thin wrappers over the GitHub CLI (`gh`) for PR review and issue triage.

ronin shells out to `gh` (which the user has already authenticated) rather than
re-implementing GitHub auth. Each wrapper fails soft — returns None / [] / False
with a reason, never raises — so a missing `gh` or network blip degrades to a
clear message instead of a crash. JSON parsing is pure and unit-tested; the
subprocess calls are mocked in tests.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def gh_available() -> bool:
    return shutil.which("gh") is not None


def _gh(*args: str, root: Path | str = ".", timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], cwd=str(Path(root).resolve()),
                          capture_output=True, text=True, timeout=timeout)


def pr_diff(number: int, root: Path | str = ".") -> str | None:
    """The unified diff of PR ``number``, or None on failure."""
    try:
        proc = _gh("pr", "diff", str(number), root=root)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def pr_comment(number: int, body: str, root: Path | str = ".") -> bool:
    """Post ``body`` as a comment on PR ``number``. True on success."""
    try:
        proc = _gh("pr", "comment", str(number), "--body", body, root=root)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def parse_issues(stdout: str) -> list[dict]:
    """Parse `gh issue list --json number,title,body,labels` output. Pure."""
    try:
        data = json.loads(stdout or "[]")
    except (ValueError, TypeError):
        return []
    out = []
    for it in data if isinstance(data, list) else []:
        out.append({
            "number": it.get("number"),
            "title": it.get("title", ""),
            "body": (it.get("body") or "")[:2000],
            "labels": [l.get("name", "") for l in it.get("labels", []) if isinstance(l, dict)],
        })
    return out


def open_issues(root: Path | str = ".", limit: int = 20) -> list[dict]:
    """Open issues (number/title/body/labels), newest first. [] on failure."""
    try:
        proc = _gh("issue", "list", "--state", "open", "--limit", str(limit),
                   "--json", "number,title,body,labels", root=root)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return parse_issues(proc.stdout)
