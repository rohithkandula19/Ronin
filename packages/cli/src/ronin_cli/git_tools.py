"""Read-only git tools for the coding agent — VCS awareness, no mutation.

The agent gets ``git_status`` / ``git_diff`` / ``git_log`` so it can reason about
what changed before it edits or commits, instead of blindly shelling out via
run_command (and triggering an approval gate for a harmless read). Mutating git
(commit/push) intentionally stays out of here: it flows through the gated
``/commit`` and ``/pr`` commands, or run_command, so a human always approves it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .git_helper import _git

# Keep tool output bounded — a giant diff would blow the context budget.
_MAX_DIFF = 12000
_MAX_LOG = 4000
# A blame over a huge range would flood the context — cap the requested span.
_MAX_BLAME_LINES = 60


def _is_repo(root) -> bool:
    return _git(root, "rev-parse", "--is-inside-work-tree").returncode == 0


@dataclass
class BlameLine:
    line: int          # final line number in the file
    commit: str        # short commit hash (8 chars)
    author: str
    date: str          # YYYY-MM-DD (author date)
    summary: str       # commit subject


def parse_blame(porcelain: str) -> list[BlameLine]:
    """Parse `git blame --porcelain` into one ``BlameLine`` per line. Pure.

    The porcelain format prints a full metadata block the first time a commit is
    seen and only the ``<sha> <orig> <final>`` header for repeat lines from that
    same commit, so we cache author/summary/date per commit and reuse them.
    Never raises — a malformed block is skipped.
    """
    out: list[BlameLine] = []
    meta: dict[str, dict[str, str]] = {}   # sha -> {author, summary, date}
    cur_sha = ""
    cur_final = 0
    pending: dict[str, str] = {}
    for raw in (porcelain or "").splitlines():
        if raw.startswith("\t"):
            # The content line — close out the current line entry.
            if cur_sha:
                if pending:
                    meta[cur_sha] = {**meta.get(cur_sha, {}), **pending}
                    pending = {}
                m = meta.get(cur_sha, {})
                out.append(BlameLine(
                    line=cur_final, commit=cur_sha[:8],
                    author=m.get("author", ""), date=m.get("date", ""),
                    summary=m.get("summary", ""),
                ))
            continue
        parts = raw.split(" ")
        if len(parts) >= 3 and len(parts[0]) == 40 and _is_hex(parts[0]):
            # Header line: "<40-hex> <orig> <final> [<count>]".
            cur_sha = parts[0]
            try:
                cur_final = int(parts[2])
            except ValueError:
                cur_final = 0
            pending = {}
            continue
        if raw.startswith("author "):
            pending["author"] = raw[len("author "):]
        elif raw.startswith("author-time "):
            pending["date"] = _fmt_epoch(raw[len("author-time "):])
        elif raw.startswith("summary "):
            pending["summary"] = raw[len("summary "):]
    return out


def _is_hex(s: str) -> bool:
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


def _fmt_epoch(raw: str) -> str:
    """Unix-epoch seconds -> 'YYYY-MM-DD'. '' if unparseable."""
    try:
        return datetime.fromtimestamp(int(raw.strip()), tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return ""


def build_git_tools(root: Path | str = ".") -> list:
    """Read-only git tools (status/diff/log) rooted at ``root``."""
    from ronin_agent_patterns import Tool

    def git_status() -> str:
        if not _is_repo(root):
            return "ERROR: not a git repository"
        branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        porcelain = _git(root, "status", "--porcelain").stdout
        if not porcelain.strip():
            return f"On branch {branch} — working tree clean"
        return f"On branch {branch}\n{porcelain.rstrip()}"

    def git_diff(path: str | None = None, staged: bool = False) -> str:
        if not _is_repo(root):
            return "ERROR: not a git repository"
        args = ["diff"]
        if staged:
            args.append("--cached")
        if path:
            args += ["--", path]
        out = _git(root, *args).stdout
        if not out.strip():
            where = "staged changes" if staged else "unstaged changes"
            return f"(no {where}{f' in {path}' if path else ''})"
        clipped = out[:_MAX_DIFF]
        if len(out) > _MAX_DIFF:
            clipped += f"\n…(diff truncated, {len(out)} chars total)"
        return clipped

    def git_log(max_count: int = 15) -> str:
        if not _is_repo(root):
            return "ERROR: not a git repository"
        max_count = max(1, min(int(max_count or 15), 100))
        out = _git(root, "log", f"-{max_count}", "--pretty=%h %s (%cr)").stdout
        return out[:_MAX_LOG].rstrip() or "(no commits yet)"

    def git_blame(path: str, line_start: int = 1, line_end: int | None = None) -> str:
        if not _is_repo(root):
            return "ERROR: not a git repository"
        if not path:
            return "ERROR: 'path' is required"
        start = max(1, int(line_start or 1))
        end = int(line_end) if line_end else start
        if end < start:
            end = start
        if end - start + 1 > _MAX_BLAME_LINES:
            end = start + _MAX_BLAME_LINES - 1   # cap the span to bound output
        proc = _git(root, "blame", "--porcelain", "-L", f"{start},{end}", "--", path)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip().splitlines()
            return f"ERROR: {err[0] if err else 'git blame failed'}"
        lines = parse_blame(proc.stdout)
        if not lines:
            return f"(no blame for {path}:{start}-{end})"
        return "\n".join(
            f"{b.line}: {b.commit} {b.date} {b.author} — {b.summary}" for b in lines
        )

    return [
        Tool(
            name="git_status",
            description="Show git working-tree status (current branch + changed/"
                        "untracked files). Read-only. Use to see what's changed before "
                        "editing or committing.",
            input_schema={"type": "object", "properties": {}},
            handler=git_status,
        ),
        Tool(
            name="git_diff",
            description="Show the git diff. Optional 'path' limits to one file; "
                        "'staged'=true shows the staged (index) diff instead of the "
                        "working-tree diff. Read-only.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "limit to this file (optional)"},
                    "staged": {"type": "boolean", "description": "show staged diff (default false)"},
                },
            },
            handler=git_diff,
        ),
        Tool(
            name="git_log",
            description="Show recent commits (hash · subject · relative date). "
                        "'max_count' caps how many (default 15). Read-only.",
            input_schema={
                "type": "object",
                "properties": {
                    "max_count": {"type": "integer", "description": "default 15, max 100"},
                },
            },
            handler=git_log,
        ),
        Tool(
            name="git_blame",
            description="Show who last changed specific lines of a file: for each line "
                        "in [line_start, line_end] returns the commit hash, author, date, "
                        "and commit subject. Use to answer 'who/why did this change'. "
                        "Read-only.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "file to blame"},
                    "line_start": {"type": "integer", "description": "first line (1-based, default 1)"},
                    "line_end": {"type": "integer", "description": "last line (default = line_start)"},
                },
                "required": ["path"],
            },
            handler=git_blame,
        ),
    ]
