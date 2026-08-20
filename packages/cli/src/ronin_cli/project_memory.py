"""Shared, local-only project memory with explicit secret rejection."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ronin_agent_patterns import Tool
from ronin_memory import LongTermMemory, SqliteBackend

from .local_embed import cosine, hashing_embed


_SECRET = re.compile(r"(?i)(?:api[_-]?key\s*[:=]|sk-[a-z0-9_-]{12,}|akia[0-9a-z]{16}|begin [^-]{0,20}private key)")
_MAX_TEXT = 8_000
_NAMESPACE = "project"
MEMORY_FILENAMES = ("RONIN.md", "CLAUDE.md", "AGENTS.md")

_TEMPLATE = """# RONIN.md

Project notes for the `ronin` coding agent. Anything here is loaded into the
agent's context on every run - keep it short and high-signal.

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
    """Return the first compatible project-instructions file, bounded in size."""
    base = Path(root)
    for name in MEMORY_FILENAMES:
        path = base / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text:
            continue
        if len(text) > _MAX_TEXT:
            text = text[:_MAX_TEXT] + "\n[project memory truncated]"
        return name, text
    return None


def memory_system_block(root: Path | str) -> tuple[str, str] | None:
    found = load_project_memory(root)
    if found is None:
        return None
    name, text = found
    return (
        f"\n\n## Project context (loaded from {name})\n"
        "The project maintainer left these notes. Treat them as authoritative "
        "for conventions, commands, and architecture:\n\n"
        f"{text}",
        name,
    )


def write_memory_template(root: Path | str) -> Path:
    """Create a starter RONIN.md without replacing a maintainer's notes."""
    path = Path(root) / "RONIN.md"
    if not path.exists():
        path.write_text(_TEMPLATE, encoding="utf-8")
    return path


def append_project_note(root: Path | str, note: str) -> tuple[Path, str]:
    """Append an explicit quick-note to the compatible instructions file."""
    base = Path(root)
    path = next((base / name for name in MEMORY_FILENAMES if (base / name).is_file()), base / "RONIN.md")
    bullet = f"- {note.strip()}\n"
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        text = ""
    if "## Notes" in text:
        lines = text.splitlines(keepends=True)
        output: list[str] = []
        inserted = False
        for line in lines:
            output.append(line)
            if not inserted and line.strip() == "## Notes":
                if not line.endswith("\n"):
                    output.append("\n")
                output.append(bullet)
                inserted = True
        text = "".join(output)
    else:
        separator = "" if not text or text.endswith("\n") else "\n"
        text = f"{text}{separator}\n## Notes\n\n{bullet}"
    path.write_text(text, encoding="utf-8")
    return path, path.name


class ProjectMemory:
    """A repository-scoped memory store. It never uses an embedding API."""

    def __init__(self, root: Path | str) -> None:
        path = Path(root).resolve() / ".ronin" / "project-memory.sqlite"
        self.memory = LongTermMemory(backend=SqliteBackend(path, score_fn=_score, max_records_per_namespace=2_000))

    def remember(self, text: str, *, source: str = "manual", tags: list[str] | tuple[str, ...] = ()) -> str:
        clean = text.strip()
        if not clean:
            raise ValueError("project memory text must not be empty")
        if len(clean) > _MAX_TEXT:
            raise ValueError("project memory entries are limited to 8,000 characters")
        if _SECRET.search(clean):
            raise ValueError("refusing to store a possible secret in project memory")
        safe_tags = [tag.strip().lower() for tag in tags if isinstance(tag, str) and tag.strip()][:24]
        return self.memory.remember(clean, namespace=_NAMESPACE, source=source[:80], tags=safe_tags)

    def recall(self, query: str, *, k: int = 5) -> list[dict[str, Any]]:
        return [
            {"id": record.id, "text": record.text, "score": record.score, "metadata": record.metadata}
            for record in self.memory.recall(query, namespace=_NAMESPACE, k=max(1, min(k, 20)))
        ]


def build_project_memory_tools(root: Path | str) -> list[Tool]:
    """Tools are explicit; writes are sensitive and pass through host approval."""
    store = ProjectMemory(root)

    def recall(query: str, k: int = 5) -> str:
        return json.dumps(store.recall(query, k=k), indent=2)

    def remember(text: str, tags: list[str] | None = None) -> str:
        try:
            record_id = store.remember(text, source="agent", tags=tags or [])
        except ValueError as exc:
            return f"refused: {exc}"
        return f"remembered project fact {record_id}"

    return [
        Tool(
            name="project_memory_recall",
            description="Recall durable local project decisions and facts. Never sends data to a provider.",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]},
            handler=recall,
        ),
        Tool(
            name="project_memory_remember",
            description="Store a durable project fact locally after approval. Refuses likely secrets.",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["text"]},
            handler=remember,
            sensitive=True,
        ),
    ]


def _score(left: str, right: str) -> float:
    return max(0.0, cosine(hashing_embed(left), hashing_embed(right)))
