"""Read-only git tools for the coding agent — VCS awareness, no mutation.

The agent gets ``git_status`` / ``git_diff`` / ``git_log`` so it can reason about
what changed before it edits or commits, instead of blindly shelling out via
run_command (and triggering an approval gate for a harmless read). Mutating git
(commit/push) intentionally stays out of here: it flows through the gated
``/commit`` and ``/pr`` commands, or run_command, so a human always approves it.
"""
from __future__ import annotations

from pathlib import Path

from .git_helper import _git

# Keep tool output bounded — a giant diff would blow the context budget.
_MAX_DIFF = 12000
_MAX_LOG = 4000


def _is_repo(root) -> bool:
    return _git(root, "rev-parse", "--is-inside-work-tree").returncode == 0


def build_git_tools(root: Path | str = ".") -> list:
    """Read-only git tools (status/diff/log) rooted at ``root``."""
    from ro_claude_kit_agent_patterns import Tool

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
    ]
