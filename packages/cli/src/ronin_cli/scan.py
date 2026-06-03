"""Secret scanning for files and staged changes — the protective half.

The write-time guard only warns the agent. This scans actual files (or what's
staged for commit) and is meant to be wired into a git pre-commit hook so a real
key can't be committed at all. Reuses the same hardening pattern set. The
file-walk and staged-file resolution are pure-ish (filesystem/git only) and
unit-tested.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .secret_guard import scan_secrets

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin"}
_TEXT_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
                  ".rb", ".sh", ".toml", ".json", ".yaml", ".yml", ".env", ".md",
                  ".txt", ".cfg", ".ini", ".tf", ".properties", ""}


@dataclass
class Finding:
    path: str
    labels: list[str]


def _scan_one(path: Path, root: Path) -> Finding | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    labels = scan_secrets(text)
    if not labels:
        return None
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    return Finding(path=rel, labels=sorted(set(labels)))


def scan_tree(root: Path | str) -> list[Finding]:
    """Scan all text files under ``root`` for secrets."""
    root_path = Path(root).resolve()
    out: list[Finding] = []
    for p in root_path.rglob("*"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix in _TEXT_SUFFIXES:
            f = _scan_one(p, root_path)
            if f:
                out.append(f)
    return out


def staged_files(root: Path | str) -> list[str]:
    """Paths of files staged for commit (added/copied/modified). [] on failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(Path(root).resolve()), "diff", "--cached",
             "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def scan_staged(root: Path | str) -> list[Finding]:
    """Scan only the files staged for commit."""
    root_path = Path(root).resolve()
    out: list[Finding] = []
    for rel in staged_files(root_path):
        p = root_path / rel
        if p.is_file():
            f = _scan_one(p, root_path)
            if f:
                out.append(f)
    return out
