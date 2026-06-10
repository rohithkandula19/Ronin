"""Run the project's linter, optionally auto-fix — `ronin lint [--fix]`.

ronin detects which linter the repo uses (ruff → flake8 for Python, eslint for
JS/TS), runs it, and reports the issue count. With --fix it first applies the
linter's own safe autofixes, then hands anything still failing to the agent to
repair. The linter detection and output parsing are pure and unit-tested; running
and the agent hand-off live in the command.
"""
from __future__ import annotations

import re
from pathlib import Path


def detect_linter(root: Path | str, *, available: set[str] | None = None) -> list[str] | None:
    """Pick a linter command for this repo. ``available`` (a set of tool names on
    PATH) can be injected for tests; otherwise everything is assumed available.
    Returns the argv list, or None if no linter fits. Pure given inputs."""
    rp = Path(root)
    have = available if available is not None else None

    def ok(tool: str) -> bool:
        return have is None or tool in have

    is_py = (rp / "pyproject.toml").is_file() or any(rp.glob("*.py")) or (rp / "setup.py").is_file()
    is_js = (rp / "package.json").is_file()

    if is_py:
        if ok("ruff"):
            return ["ruff", "check", "."]
        if ok("flake8"):
            return ["flake8"]
    if is_js and ok("npx"):
        return ["npx", "eslint", "."]
    return None


def fix_command(cmd: list[str]) -> list[str] | None:
    """The linter's own safe-autofix invocation, if it has one. Pure."""
    if not cmd:
        return None
    if cmd[:2] == ["ruff", "check"]:
        return ["ruff", "check", "--fix", "."]
    if cmd[:2] == ["npx", "eslint"]:
        return ["npx", "eslint", ".", "--fix"]
    return None


def parse_issue_count(output: str, cmd: list[str]) -> int:
    """Best-effort issue count from linter output. Pure."""
    tool = cmd[0] if cmd else ""
    if tool == "ruff":
        m = re.search(r"Found (\d+) error", output)
        if m:
            return int(m.group(1))
        # "ruff check" with no summary line: count rule-coded lines (path:line:col: CODE)
        return len(re.findall(r"^\S+:\d+:\d+:\s+[A-Z]\d+", output, re.MULTILINE))
    if tool == "flake8":
        return len([ln for ln in output.splitlines() if re.match(r"\S+:\d+:\d+:", ln)])
    if tool == "npx":  # eslint summary: "✖ 12 problems"
        m = re.search(r"(\d+)\s+problems?", output)
        return int(m.group(1)) if m else 0
    return 0
