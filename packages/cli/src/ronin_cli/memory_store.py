"""Persistent, cross-session memory for ronin — it remembers *you*.

Durable facts and preferences ("my name is Rohith", "I use Groq", "my main repo
is ~/ronin", "I prefer tabs") are saved to a user-global JSON file at
``~/.ronin/memory.json`` and injected into the agent's system prompt on every
future run — so a brand-new `ronin` next week already knows you.

The agent saves facts via the ``remember`` tool; recall is by injecting the most
recent facts into context (plus a Jaccard search via the kit's LongTermMemory
when you want relevance). No vector DB, no extra deps.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ronin_agent_patterns import Tool

_MAX_STORED = 300
_INJECT_RECENT = 40
_RECALL_K = 8                 # top-K relevant facts returned by recall()
_AUTO_RECALL_K = 6            # top-K relevant facts auto-injected per turn
_AUTO_RECALL_CHARS = 1200     # hard cap on the auto-recall block size


def _memory_path() -> Path:
    """Location of the memory file. Honors ``RONIN_HOME`` (the same override
    bushido and the run store use), defaulting to ``~/.ronin``."""
    home = os.environ.get("RONIN_HOME")
    base = Path(home) if home else Path.home() / ".ronin"
    return base / "memory.json"


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
    """Persist facts to the user-global memory file, owner-readable only.

    The file holds durable facts the user chose to store — never secrets:
    :func:`add_memory` refuses anything the secret scanner flags, so a key cannot
    reach this write. It is still user data on disk, so the file is created 0600
    (owner read/write) rather than inheriting a permissive umask.
    """
    p = _memory_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps({"memories": memories[-_MAX_STORED:]}, indent=2), encoding="utf-8")
        os.chmod(p, 0o600)
    except OSError:
        pass


def secret_labels(text: str) -> list[str]:
    """The kinds of secret detected in ``text`` (e.g. ``['anthropic-key']``), empty
    when clean. Obvious placeholders (``EXAMPLE`` / ``REDACTED``) are not secrets."""
    from .secret_guard import scan_secrets
    return scan_secrets(text)


def add_memory(text: str) -> bool:
    """Save a durable fact. Returns False if blank, a near-duplicate, or if the
    text carries a secret.

    The secret floor lives HERE, at the store, not only in the CLI — every caller
    (the ``ronin remember`` command *and* the agent's own ``remember`` tool) is
    covered. This matters because memories are injected into the system prompt on
    every future run: a stored key would be re-sent to the provider forever.
    """
    text = " ".join(text.split()).strip()
    if not text:
        return False
    if secret_labels(text):
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


def list_memories() -> list[str]:
    """Just the fact strings, newest last. Thin wrapper over load_memories()."""
    return [m.get("text", "") for m in load_memories() if m.get("text")]


# --------------------------------------------------------------------------- #
# Relevance: recall(query) and targeted forget(match)
# --------------------------------------------------------------------------- #
# Very common words carry no signal for matching a fact to a query. Dropping them
# stops "what do I use" from matching every fact that contains "do" or "i".
_STOPWORDS = frozenset(
    "a an the and or of to in on for with my me i you your is are am be do does "
    "did how what when where who why which that this it its at as by from".split()
)


def _stem(word: str) -> str:
    """Crudest possible stemmer: strip a few common English suffixes so 'uses',
    'using', and 'used' all collapse to 'us...'-ish roots and match 'use'. Good
    enough for keyword recall; no nltk, no data files."""
    for suf in ("ing", "ed", "es", "s"):
        if len(word) > len(suf) + 2 and word.endswith(suf):
            return word[: -len(suf)]
    return word


def _stems(words) -> set[str]:
    return {_stem(w) for w in words if w not in _STOPWORDS and len(w) > 1}


def _score(query_terms: list[str], text: str) -> float:
    """Relevance of one fact to a query. Jaccard over STEMMED, stop-word-filtered
    word sets, plus a small overlap-count nudge so a fact that shares more query
    words ranks higher. Pure, dependency-free."""
    qset = _stems(query_terms)
    words = _stems((text or "").lower().split())
    if not qset or not words:
        return 0.0
    overlap = qset & words
    if not overlap:
        return 0.0
    jaccard = len(overlap) / len(qset | words)
    return jaccard + 0.01 * len(overlap)


def recall(query: str, k: int = _RECALL_K) -> list[str]:
    """The facts most relevant to ``query``, best first. Stemmed keyword/Jaccard
    match; zero-overlap facts are dropped. Returns [] when nothing is relevant or
    the query is empty. No network, no model."""
    from .recall import tokenize

    terms = tokenize(query)
    if not terms:
        return []
    scored = [(text, _score(terms, text)) for text in list_memories()]
    hits = [(t, s) for t, s in scored if s > 0]
    hits.sort(key=lambda ts: ts[1], reverse=True)
    return [t for t, _ in hits[:k]]


def forget(match: str) -> int:
    """Drop every stored fact that contains ``match`` (case-insensitive
    substring). Returns the number removed. A blank match removes nothing."""
    match = " ".join((match or "").split()).strip().lower()
    if not match:
        return 0
    memories = load_memories()
    kept = [m for m in memories if match not in m.get("text", "").lower()]
    removed = len(memories) - len(kept)
    if removed:
        _save(kept)
    return removed


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


def relevant_prompt_block(query: str, *, k: int = _AUTO_RECALL_K,
                          max_chars: int = _AUTO_RECALL_CHARS) -> str:
    """Auto-recall: the facts most relevant to ``query``, as a system-prompt block.

    Used at the START of an agent turn so the agent walks in already knowing the
    relevant things about the user (name, stack, repos, preferences) instead of
    re-asking. Bounded by design: at most ``k`` facts and ``max_chars`` total, so
    it never bloats the prompt. Returns '' when nothing is relevant, so a fresh
    or off-topic message adds zero context. No network, no model.
    """
    facts = recall(query, k=k)
    if not facts:
        return ""
    lines: list[str] = []
    used = 0
    for fact in facts:
        line = f"- {fact}"
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return (
        "\n\n## What you already know about the user (relevant to this request)\n"
        "Use these durable facts so you don't re-ask what you already know; "
        "don't repeat them back unprompted.\n"
        + "\n".join(lines)
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


def build_recall_tool() -> Tool:
    """A tool the agent calls to look up what it already knows about the user."""

    def recall_tool(query: str) -> str:
        facts = recall(query, k=_RECALL_K)
        if not facts:
            return f"Nothing in long-term memory about {query!r}."
        return "What I remember about the user:\n" + "\n".join(f"- {f}" for f in facts)

    return Tool(
        name="recall_memory",
        description=(
            "Search long-term memory for durable facts about the user relevant to a "
            "query (their name, stack, repos, preferences). Use this before asking the "
            "user something you may already know. Returns the most relevant stored facts."
        ),
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to look up."}},
            "required": ["query"],
        },
        handler=recall_tool,
    )


def build_forget_tool() -> Tool:
    """A tool the agent calls to drop stored facts the user asks it to forget."""

    def forget_tool(match: str) -> str:
        n = forget(match)
        return f"Forgot {n} memory item(s) matching {match!r}." if n else f"Nothing stored matched {match!r}."

    return Tool(
        name="forget_memory",
        description=(
            "Remove durable facts from long-term memory that match a phrase "
            "(case-insensitive substring). Use when the user says to forget something "
            "about them. Returns how many facts were removed."
        ),
        input_schema={
            "type": "object",
            "properties": {"match": {"type": "string", "description": "Phrase to match facts to remove."}},
            "required": ["match"],
        },
        handler=forget_tool,
    )
