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

from ronin_agent_patterns import Tool

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


_EXTRACT_SYSTEM = (
    "You extract DURABLE facts about the USER worth remembering across future "
    "sessions — their name, role, tech stack, the projects/repos they work in, "
    "preferences, recurring goals. IGNORE ephemeral or one-off details, greetings, "
    "questions, and anything about the assistant. Output ONLY a JSON array of short "
    "fact strings (e.g. [\"User's name is Rohith\", \"Prefers Python\"]). Use [] if nothing durable."
)


def _parse_json_list(text: str) -> list[str]:
    import json as _json
    import re
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return []
    try:
        data = _json.loads(m.group(0))
    except ValueError:
        return []
    return [str(x).strip() for x in data if isinstance(x, (str,)) and str(x).strip()]


def auto_extract(config, exchange: str) -> int:
    """Best-effort: ask the model to pull durable user facts from one exchange and
    save them. Returns the number of new facts stored. Silent on any failure
    (rate limits, parse errors) — memory is never allowed to break a turn."""
    try:
        from ronin_agent_patterns import Message

        from .runner import build_provider
        provider = build_provider(config)
        resp = provider.complete(
            system=_EXTRACT_SYSTEM,
            messages=[Message(role="user", content=f"Exchange:\n{exchange[:4000]}")],
            tools=[],
            max_tokens=300,
        )
        facts = _parse_json_list(resp.text)
    except Exception:  # noqa: BLE001 — best-effort, never raise
        return 0
    return sum(1 for f in facts if add_memory(f))


def auto_extract_background(config, exchange: str) -> None:
    """Run auto_extract in a daemon thread so it never adds latency to a turn."""
    import threading
    threading.Thread(target=auto_extract, args=(config, exchange), daemon=True).start()


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
