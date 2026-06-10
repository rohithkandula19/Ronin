"""Code archaeology — why is this code the way it is?

`ronin archaeology <file>` reconstructs a file's story from git history (the
commits that shaped it, who, when, why) and hands it to the agent to explain:
what each significant change was for, what problem it solved, and any gotchas the
history reveals. Onboarding gold. The log parsing is pure and unit-tested.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_SEP = "\x1f"   # unit separator — safe field delimiter inside commit data


def parse_log(output: str) -> list[dict]:
    """Parse ``git log`` lines of the form ``sha<US>short<US>author<US>date<US>subject``."""
    rows: list[dict] = []
    for line in output.splitlines():
        parts = line.split(_SEP)
        if len(parts) == 5:
            rows.append({"sha": parts[0], "short": parts[1], "author": parts[2],
                         "date": parts[3], "subject": parts[4]})
    return rows


def file_history(root: Path | str, path: str, *, limit: int = 25) -> list[dict]:
    """Commits that touched ``path`` (following renames), newest first."""
    fmt = _SEP.join(["%H", "%h", "%an", "%ad", "%s"])
    try:
        proc = subprocess.run(
            ["git", "-C", str(Path(root).resolve()), "log", "--follow", f"--pretty={fmt}",
             "--date=short", f"-{limit}", "--", path],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_log(proc.stdout) if proc.returncode == 0 else []


def history_brief(commits: list[dict]) -> str:
    """A compact timeline string for the agent prompt / display. Pure."""
    return "\n".join(f"{c['date']}  {c['short']}  {c['author']:<16}  {c['subject']}"
                     for c in commits)
