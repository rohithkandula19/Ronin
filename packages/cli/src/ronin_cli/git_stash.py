"""Safe, AI-labelled git stash — pure core for ``ronin stash``.

``ronin stash`` wraps ``git stash`` with human-readable, AI-summarized labels so a
stash list reads like a changelog instead of "WIP on main: <hash>". The summary is
drafted from the diff by the configured provider, with a deterministic fallback
when there is no key or network.

This module is the PURE core: parsing ``git stash list`` output, summarizing a diff
into a one-line label (with a no-LLM fallback), and formatting the list for display.
The git mutation itself lives in the typer command in ``main.py`` and only ever runs
as an explicit user command — never the agent acting on its own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

# A drafter takes (system, prompt) and returns a one-line summary, or raises.
Drafter = Callable[[str, str], str]

_STASH_LINE = re.compile(r"^(stash@\{(\d+)\}):\s*(.*)$")


@dataclass(frozen=True)
class StashEntry:
    """One parsed line of ``git stash list``."""

    ref: str          # e.g. "stash@{0}"
    index: int        # e.g. 0
    branch: str       # branch the stash was taken on, or "" if not parseable
    label: str        # the human-readable message (our summary, or git's default)


def parse_stash_list(output: str) -> list[StashEntry]:
    """Parse ``git stash list`` output into structured entries. Pure.

    Each line looks like ``stash@{0}: On main: <message>`` or
    ``stash@{1}: WIP on main: <hash> <subject>``. We pull out the ref/index, the
    branch (the token after ``On``/``WIP on``), and the trailing message.
    """
    entries: list[StashEntry] = []
    for raw in output.splitlines():
        line = raw.rstrip()
        m = _STASH_LINE.match(line)
        if not m:
            continue
        ref, index_s, rest = m.group(1), m.group(2), m.group(3)
        branch, label = _split_branch_message(rest)
        entries.append(StashEntry(ref=ref, index=int(index_s), branch=branch, label=label))
    return entries


def _split_branch_message(rest: str) -> tuple[str, str]:
    """From ``On main: ...`` / ``WIP on main: ...`` pull (branch, message)."""
    m = re.match(r"^(?:WIP on|On)\s+([^:]+):\s*(.*)$", rest)
    if not m:
        return "", rest.strip()
    return m.group(1).strip(), m.group(2).strip()


def fallback_label(diff_stat: str, file_names: list[str]) -> str:
    """Deterministic one-line label when there is no provider/network. Pure.

    Built from the ``git diff --stat`` summary line plus a couple of file names, so
    it still reads better than git's default ``WIP on <branch>: <hash>``.
    """
    files = [f for f in file_names if f.strip()]
    n = len(files)
    head = ", ".join(files[:3])
    if n > 3:
        head += f" (+{n - 3} more)"
    if not head:
        head = "working tree"
    insertions, deletions = _parse_diffstat_totals(diff_stat)
    churn = ""
    if insertions or deletions:
        churn = f" [+{insertions}/-{deletions}]"
    noun = "file" if n == 1 else "files"
    return f"wip: {n} {noun} — {head}{churn}"


def _parse_diffstat_totals(diff_stat: str) -> tuple[int, int]:
    """Pull (insertions, deletions) from a ``git diff --stat`` summary line.

    The summary line looks like ``3 files changed, 12 insertions(+), 4 deletions(-)``.
    Missing parts default to 0.
    """
    ins = re.search(r"(\d+) insertion", diff_stat)
    dele = re.search(r"(\d+) deletion", diff_stat)
    return (int(ins.group(1)) if ins else 0, int(dele.group(1)) if dele else 0)


def _clean_summary(text: str) -> str:
    """Normalize a model-drafted label to a single tidy line."""
    line = text.strip().strip("`").strip()
    # Take only the first line, drop a leading bullet/quote if the model added one.
    line = line.splitlines()[0] if line else ""
    line = line.lstrip("-*> ").strip().strip('"').strip()
    # Keep it short enough to fit a stash list comfortably.
    if len(line) > 80:
        line = line[:77].rstrip() + "..."
    return line


def summarize_diff(
    diff: str,
    diff_stat: str,
    file_names: list[str],
    drafter: Optional[Drafter] = None,
) -> str:
    """Summarize a working-tree diff into a one-line stash label. Pure-ish.

    If ``drafter`` is provided it is called with (system, prompt) and its result is
    cleaned to a single line. Any exception, an empty result, or no drafter at all
    falls back to the deterministic :func:`fallback_label`, so this never raises and
    is safe to call offline.
    """
    if drafter is not None and diff.strip():
        try:
            raw = drafter(
                "You write terse one-line summaries of code changes for a git stash label.",
                "Summarize these working-tree changes as ONE short imperative line "
                "(under 72 chars, no quotes, no trailing period). Output only the line.\n\n"
                + diff[:6000],
            )
            cleaned = _clean_summary(raw)
            if cleaned:
                return cleaned
        except Exception:  # noqa: BLE001 — degrade gracefully to the static label
            pass
    return fallback_label(diff_stat, file_names)


def format_stash_list(entries: list[StashEntry]) -> str:
    """Render parsed stash entries as aligned plain text. Pure.

    Returns a single string (no trailing newline). Empty input yields "".
    """
    if not entries:
        return ""
    lines = []
    for e in entries:
        branch = f"[{e.branch}] " if e.branch else ""
        lines.append(f"stash@{{{e.index}}}  {branch}{e.label}")
    return "\n".join(lines)
