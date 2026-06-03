"""Schedule ronin on the system crontab — make 'while you sleep' literal.

The pure core (upsert/remove a marked line in crontab text) is unit-tested; the
thin wrappers shell out to ``crontab -l`` / ``crontab -`` to read and write. Each
managed entry carries a marker comment so install is idempotent and uninstall
removes only ours.
"""
from __future__ import annotations

import subprocess

MARKER = "# ronin-scheduled"


def cron_line(schedule: str, command: str) -> str:
    """A crontab line: '<schedule> cd <cwd> && <command>  <marker>' — no cwd here;
    the caller bakes any directory into ``command``. Pure."""
    return f"{schedule.strip()} {command.strip()}  {MARKER}"


def upsert_cron(existing: str, schedule: str, command: str) -> str:
    """Return crontab text with ronin's marked line added or replaced. Pure."""
    line = cron_line(schedule, command)
    kept = [ln for ln in existing.splitlines() if MARKER not in ln]
    kept = [ln for ln in kept if ln.strip() != ""] or []
    kept.append(line)
    return "\n".join(kept) + "\n"


def remove_cron(existing: str) -> tuple[str, bool]:
    """Return (crontab text without ronin's line, removed?). Pure."""
    lines = existing.splitlines()
    kept = [ln for ln in lines if MARKER not in ln]
    removed = len(kept) != len(lines)
    text = "\n".join(ln for ln in kept if ln.strip() != "")
    return (text + "\n" if text else ""), removed


def current_entry(existing: str) -> str | None:
    """ronin's scheduled line from crontab text, or None. Pure."""
    for ln in existing.splitlines():
        if MARKER in ln:
            return ln
    return None


# --- thin subprocess wrappers (not unit-tested; they touch the real crontab) ---

def read_crontab() -> str:
    try:
        proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout if proc.returncode == 0 else ""   # no crontab yet → empty


def write_crontab(text: str) -> bool:
    try:
        proc = subprocess.run(["crontab", "-"], input=text, capture_output=True,
                              text=True, timeout=15)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def install(schedule: str, command: str) -> bool:
    return write_crontab(upsert_cron(read_crontab(), schedule, command))


def uninstall() -> bool:
    text, removed = remove_cron(read_crontab())
    if not removed:
        return False
    return write_crontab(text)
