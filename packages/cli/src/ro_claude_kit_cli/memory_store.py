"""Persistent, cross-session memory for ronin — it remembers *you*.

Durable facts and preferences ("my name is Rohith", "I use Groq", "my main repo
is ~/ronin", "I prefer tabs") are saved to a user-global JSON file at
``~/.csk/memory.json`` and injected into the agent's system prompt on every
future run — so a brand-new `ronin` next week already knows you.

The agent saves facts via the ``remember`` tool; recall is by injecting the most
recent facts into context (plus a Jaccard search via the kit's LongTermMemory
when you want relevance). No vector DB, no extra deps.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ro_claude_kit_agent_patterns import Tool

_MAX_STORED = 300
_INJECT_RECENT = 40


def _memory_path() -> Path:
    return Path.home() / ".csk" / "memory.json"


def load_memories() -> list[dict]:
    p = _memory_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return list(data.get("memories", []))
    except (OSError, ValueError):
        return []


def _save(memories: list[dict]) -> None:
    p = _memory_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps({"memories": memories[-_MAX_STORED:]}, indent=2), encoding="utf-8")
    except OSError:
        pass


def add_memory(text: str) -> bool:
    """Save a durable fact. Returns False if blank or a near-duplicate."""
    text = " ".join(text.split()).strip()
    if not text:
        return False
    memories = load_memories()
    if any(m.get("text", "").lower() == text.lower() for m in memories):
        return False
    memories.append({"text": text, "ts": time.time()})
    _save(memories)
    return True


def forget_all() -> int:
    n = len(load_memories())
    _save([])
    return n


def memory_prompt_block(limit: int = _INJECT_RECENT) -> str:
    """The '## What you remember about the user' block for the system prompt."""
    memories = load_memories()
    if not memories:
        return ""
    recent = memories[-limit:]
    lines = "\n".join(f"- {m['text']}" for m in recent)
    return (
        "\n\n## What you remember about the user (from past sessions)\n"
        "Use these durable facts when relevant; don't repeat them back unprompted.\n"
        f"{lines}"
    )


def build_remember_tool() -> Tool:
    """A tool the agent calls to persist a durable fact about the user."""

    def remember(fact: str) -> str:
        return f"Saved to long-term memory: {fact}" if add_memory(fact) else "Already in memory."

    return Tool(
        name="remember",
        description=(
            "Save a DURABLE fact or preference about the user to long-term memory so "
            "future sessions recall it — e.g. their name, tech stack, the repos they "
            "work in, coding preferences, recurring goals. Call this whenever the user "
            "shares something worth remembering across sessions. Do NOT save ephemeral "
            "or one-off details."
        ),
        input_schema={
            "type": "object",
            "properties": {"fact": {"type": "string", "description": "The durable fact, one sentence."}},
            "required": ["fact"],
        },
        handler=remember,
    )
