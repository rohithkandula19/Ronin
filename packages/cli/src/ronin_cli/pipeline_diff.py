"""Read-only unified-diff capture for evidence-based pipeline verification.

Captures the working tree's actual diff so the verifier and semantic-contract
stages reason about real evidence, not the implementer's self-reported
``diff_summary``. It covers **tracked** changes vs HEAD (staged + unstaged) AND
brand-new **untracked** files.

Safety: this is strictly read-only. Untracked files are captured with
``git diff --no-index`` (which never touches the index or the working tree), so
there is **no ``git add`` and nothing to clean up** — we never stage a file,
never write a file, and never destroy local work. Binary or oversized untracked
files are recorded as metadata only. Any capture failure is reported in
``capture_warnings`` and the tracked evidence still stands. It never raises.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# per-file byte cap for including an untracked file's full text in the excerpt
_UNTRACKED_FILE_MAX = 20000


class DiffEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    captured: bool = False              # did we obtain a diff at all?
    files_changed: list[str] = Field(default_factory=list)   # tracked + untracked
    tracked_files: list[str] = Field(default_factory=list)
    untracked_files: list[str] = Field(default_factory=list)
    binary_files: list[str] = Field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    truncated: bool = False
    diff_excerpt: str = ""
    full_diff_available: bool = False   # a non-empty diff existed (excerpt may be trimmed)
    omitted_files: list[str] = Field(default_factory=list)   # binary/oversized, metadata only
    capture_warnings: list[str] = Field(default_factory=list)
    note: str = ""                      # honest explanation when nothing was captured


def _git(root, *args, timeout: int = 20) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _looks_binary(path: Path) -> bool:
    """Cheap binary sniff — a NUL byte in the first 8 KB."""
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(8192)
    except OSError:
        return False


def _capture_untracked(root: Path, context: int, ev: DiffEvidence) -> str:
    """Append untracked (new) files to the evidence. Returns their combined diff
    text. Read-only: uses ``git diff --no-index`` (no index mutation). Binary or
    oversized files are recorded as metadata only. Failures degrade honestly."""
    listing = _git(root, "ls-files", "--others", "--exclude-standard")
    if listing is None or listing.returncode != 0:
        ev.capture_warnings.append("could not list untracked files")
        return ""
    parts: list[str] = []
    for rel in listing.stdout.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        fpath = root / rel
        try:
            size = fpath.stat().st_size
        except OSError:
            ev.capture_warnings.append(f"could not stat untracked file: {rel}")
            continue
        ev.untracked_files.append(rel)
        if _looks_binary(fpath):
            ev.binary_files.append(rel)
            ev.omitted_files.append(rel)
            continue
        if size > _UNTRACKED_FILE_MAX:
            ev.omitted_files.append(rel)
            ev.capture_warnings.append(f"untracked file too large, metadata only: {rel} ({size}B)")
            continue
        # read-only diff of an empty tree vs the new file (returncode 1 == "differs")
        proc = _git(root, "diff", "--no-index", f"-U{max(0, context)}",
                    "--", "/dev/null", rel)
        if proc is None or proc.returncode > 1:
            ev.omitted_files.append(rel)
            ev.capture_warnings.append(f"could not diff untracked file: {rel}")
            continue
        text = proc.stdout
        if text.strip():
            parts.append(text)
            ev.additions += sum(1 for ln in text.splitlines()
                                if ln.startswith("+") and not ln.startswith("+++"))
    return "\n".join(parts)


def capture_diff(root, *, context: int = 3, max_bytes: int = 20000,
                 include: bool = True, include_untracked: bool = True) -> DiffEvidence:
    """Capture the working-tree diff (read-only). Never raises.

    Covers tracked changes vs HEAD plus brand-new untracked files (unless
    ``include_untracked=False``). ``include=False`` → ``--no-diff-evidence``."""
    if not include:
        return DiffEvidence(captured=False, note="diff evidence disabled")

    root = Path(str(Path(root).resolve()))
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.returncode != 0:
        return DiffEvidence(captured=False, note="not a git repository")

    ev = DiffEvidence(captured=True)

    # 1) tracked changes vs HEAD (staged + unstaged)
    numstat = _git(root, "diff", "--numstat", "HEAD")
    if numstat is not None and numstat.returncode == 0:
        for line in numstat.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            a, d, path = parts
            path = path.strip()
            ev.tracked_files.append(path)
            if a == "-" and d == "-":       # binary
                ev.binary_files.append(path)
                if path not in ev.omitted_files:
                    ev.omitted_files.append(path)
                continue
            if a.isdigit():
                ev.additions += int(a)
            if d.isdigit():
                ev.deletions += int(d)

    tracked_diff = _git(root, "diff", f"-U{max(0, context)}", "HEAD")
    tracked_text = tracked_diff.stdout if (tracked_diff is not None
                                           and tracked_diff.returncode == 0) else ""

    # 2) untracked (new) files, read-only
    untracked_text = _capture_untracked(root, context, ev) if include_untracked else ""

    ev.files_changed = ev.tracked_files + [u for u in ev.untracked_files
                                           if u not in ev.tracked_files]

    combined = "\n".join(t for t in (tracked_text, untracked_text) if t.strip())
    if not combined.strip():
        ev.full_diff_available = False
        ev.note = "no tracked or untracked changes in the working tree"
        return ev

    ev.full_diff_available = True
    ev.truncated = len(combined) > max_bytes if max_bytes >= 0 else False
    ev.diff_excerpt = (combined[:max_bytes] + "\n… [diff truncated]") if ev.truncated else combined
    if ev.truncated:
        ev.note = "diff truncated to the byte budget"
    return ev
