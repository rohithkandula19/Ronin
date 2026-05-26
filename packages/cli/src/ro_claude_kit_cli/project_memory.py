"""Project memory — the ``CLAUDE.md`` idea for ``ronin``.

Before working, the coding agent loads a project notes file from the repo root
and folds it into the system prompt, so it follows your conventions, build
commands, and architecture instead of guessing. We look for these names, in
order (first found wins):

    RONIN.md   — ronin's own file
    CLAUDE.md  — interop: works in any repo that already has one
    AGENTS.md  — the emerging cross-tool standard

The file is capped so a huge doc can't blow the context budget.
"""
from __future__ import annotations

from pathlib import Path

MEMORY_FILENAMES = ("RONIN.md", "CLAUDE.md", "AGENTS.md")
_MAX_CHARS = 8000

_TEMPLATE = """# RONIN.md

Project notes for the `ronin` coding agent. Anything here is loaded into the
agent's context on every run — keep it short and high-signal.

## What this project is
<one or two sentences>

## Commands
- Install:
- Test:
- Lint / typecheck:
- Run:

## Conventions
- <style, patterns, things to always/never do>

## Architecture
- <key directories and what lives where>
"""


def load_project_memory(root: Path | str) -> tuple[str, str] | None:
    """Return ``(filename, contents)`` for the first memory file found in
    ``root``, or ``None``. Contents are truncated to a safe size."""
    base = Path(root)
    for name in MEMORY_FILENAMES:
        path = base / name
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not text:
                continue
            if len(text) > _MAX_CHARS:
                text = text[:_MAX_CHARS] + "\n…[project memory truncated]"
            return name, text
    return None


def memory_system_block(root: Path | str) -> tuple[str, str] | None:
    """Build the system-prompt block for project memory, plus the filename.

    Returns ``(block, filename)`` or ``None`` if no memory file exists.
    """
    found = load_project_memory(root)
    if found is None:
        return None
    name, text = found
    block = (
        f"\n\n## Project context (loaded from {name})\n"
        "The project maintainer left these notes. Treat them as authoritative "
        "for conventions, commands, and architecture:\n\n"
        f"{text}"
    )
    return block, name


def write_memory_template(root: Path | str) -> Path:
    """Create a starter RONIN.md in ``root`` (does not overwrite)."""
    path = Path(root) / "RONIN.md"
    if not path.exists():
        path.write_text(_TEMPLATE, encoding="utf-8")
    return path
