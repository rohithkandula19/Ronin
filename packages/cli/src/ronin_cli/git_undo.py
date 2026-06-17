"""Explain-and-revert the last commit — pure core for ``ronin undo-commit``.

``ronin undo-commit`` shows the last commit (hash, author, subject, changed files,
insertions/deletions) and then offers a gated choice:

- soft-undo  (``git reset --soft HEAD~1``) — keeps your changes staged (default,
  non-destructive), or
- revert     (``git revert``) — records a new commit that undoes it.

History is only ever rewritten behind an explicit Confirm prompt, and the
soft-undo refuses to run when HEAD has been pushed to a remote branch unless the
user passes ``--force`` (so shared history is not silently rewritten).

This module is the PURE core: parsing the commit info from a ``git show`` payload
and deciding whether HEAD is on a pushed branch. The git mutations live in the
typer command in ``main.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# A sentinel we put in the --format so the header is unambiguous to parse, even if
# a commit subject happens to contain newlines or stat-like text.
HEADER_SENTINEL = "@@RONIN@@"

# The format string the command should pass to `git show`. Fields are sentinel
# separated on one logical line: hash, short hash, author, date, subject.
SHOW_FORMAT = HEADER_SENTINEL + "%H" + HEADER_SENTINEL + "%h" + HEADER_SENTINEL \
    + "%an" + HEADER_SENTINEL + "%ad" + HEADER_SENTINEL + "%s"


@dataclass(frozen=True)
class CommitInfo:
    """Parsed summary of a single commit."""

    sha: str
    short_sha: str
    author: str
    date: str
    subject: str
    files: list[str] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0

    @property
    def file_count(self) -> int:
        return len(self.files)


def parse_commit_info(show_output: str) -> CommitInfo | None:
    """Parse ``git show --stat --format=SHOW_FORMAT`` output into a CommitInfo. Pure.

    Returns ``None`` if the sentinel header is missing (e.g. empty repo / bad input).
    """
    header_line = ""
    for line in show_output.splitlines():
        if line.startswith(HEADER_SENTINEL):
            header_line = line
            break
    if not header_line:
        return None

    parts = header_line.split(HEADER_SENTINEL)
    # parts[0] is "" (text before the leading sentinel); fields follow.
    fields = parts[1:]
    if len(fields) < 5:
        return None
    sha, short_sha, author, date, subject = (f.strip() for f in fields[:5])

    files = _parse_changed_files(show_output)
    insertions, deletions = _parse_shortstat(show_output)
    return CommitInfo(
        sha=sha, short_sha=short_sha, author=author, date=date, subject=subject,
        files=files, insertions=insertions, deletions=deletions,
    )


def _parse_changed_files(show_output: str) -> list[str]:
    """Pull the changed-file names from the --stat block. Pure.

    Stat lines look like ``  path/to/file.py | 12 +++---`` ending before the
    ``N files changed`` summary. We keep order and drop the summary line.
    """
    files: list[str] = []
    for line in show_output.splitlines():
        if "|" not in line:
            continue
        left = line.split("|", 1)[0].strip()
        if not left or left.startswith(HEADER_SENTINEL):
            continue
        # Skip the binary-rename arrow form's right side; left side is the path.
        # A rename shows as "old => new"; keep the new name for clarity.
        if "=>" in left:
            left = left.split("=>")[-1].strip().rstrip("}")
        files.append(left)
    return files


def _parse_shortstat(show_output: str) -> tuple[int, int]:
    """Pull (insertions, deletions) from the ``N files changed, ...`` summary. Pure.

    Only the diffstat *summary* line is scanned (``N files? changed, ...``), so a
    commit subject that itself mentions "insertions" does not throw off the counts.
    """
    summary = ""
    for line in show_output.splitlines():
        if re.search(r"\d+ files? changed", line):
            summary = line
            break
    if not summary:
        return (0, 0)
    ins = re.search(r"(\d+) insertion", summary)
    dele = re.search(r"(\d+) deletion", summary)
    return (int(ins.group(1)) if ins else 0, int(dele.group(1)) if dele else 0)


def is_pushed(upstream_sha: str, head_sha: str, head_in_upstream: bool) -> bool:
    """Decide whether HEAD has been pushed (so rewriting it is dangerous). Pure.

    ``upstream_sha`` is the resolved SHA of the upstream tracking ref (e.g. from
    ``git rev-parse @{upstream}``), or "" when there is no upstream. ``head_sha`` is
    HEAD's SHA. ``head_in_upstream`` is the caller's ancestry result (e.g.
    ``git merge-base --is-ancestor HEAD @{upstream}`` succeeded), which is True when
    HEAD is contained in the upstream — meaning the commit has been pushed.

    With no upstream, the commit cannot have been pushed, so we return False. We
    treat HEAD as pushed when it equals the upstream tip or is an ancestor of it.
    """
    upstream_sha = (upstream_sha or "").strip()
    head_sha = (head_sha or "").strip()
    if not upstream_sha or not head_sha:
        return False
    return upstream_sha == head_sha or head_in_upstream


def format_commit_info(info: CommitInfo) -> str:
    """Render a CommitInfo as plain text for display. Pure."""
    files = info.files
    shown = files[:8]
    file_lines = "\n".join(f"  {f}" for f in shown)
    if len(files) > 8:
        file_lines += f"\n  (+{len(files) - 8} more)"
    noun = "file" if info.file_count == 1 else "files"
    return (
        f"{info.short_sha}  {info.subject}\n"
        f"author: {info.author}  ({info.date})\n"
        f"{info.file_count} {noun} changed, +{info.insertions}/-{info.deletions}\n"
        + (file_lines if file_lines else "  (no file changes)")
    )
