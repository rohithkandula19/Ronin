"""Explicit user-memory commands, parsed deterministically (no model call).

A phone message like "remember that I use Groq", "forget that I use Groq", or
"what do you remember" is cheap and unambiguous. It should not cost a model
turn. This module parses such a message into an intent the Telegram bridge (and
any other front-end) can act on directly against ``memory_store``.

Pure and stdlib-only: ``parse_memory_op(text)`` returns a ``MemoryOp`` or None.
None means "not an explicit memory command, fall through to the agent". The
acting code lives next to the caller so this stays trivially testable offline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# kind in {"remember", "forget", "list"}; payload is the fact/match (empty for list).


@dataclass
class MemoryOp:
    kind: str
    payload: str = ""


# "remember (that) <fact>" / "note (that) <fact>" / "keep in mind (that) <fact>"
_REMEMBER = re.compile(
    r"^\s*(?:please\s+)?(?:remember|note|keep in mind)(?:\s+that)?\b[:,]?\s*(?P<fact>.+)$",
    re.IGNORECASE | re.DOTALL,
)
# "forget (that) <match>" / "forget about <match>"
_FORGET = re.compile(
    r"^\s*(?:please\s+)?forget(?:\s+(?:that|about))?\b[:,]?\s*(?P<match>.+)$",
    re.IGNORECASE | re.DOTALL,
)
# "what do you remember (about me)?" / "what do you know about me" /
# "list (my )memor(y|ies)" / "show (my )memory"
_LIST = re.compile(
    r"^\s*(?:"
    r"what\s+do\s+you\s+(?:remember|know)(?:\s+about\s+me)?"
    r"|list\s+(?:my\s+)?memor(?:y|ies)"
    r"|show\s+(?:my\s+)?memor(?:y|ies)"
    r")\s*[?.!]*\s*$",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return " ".join((text or "").split()).strip()


def parse_memory_op(text: str) -> MemoryOp | None:
    """Parse an explicit memory command, or None if this is not one.

    Order matters: the list/query form is checked first (so "forget" inside a
    longer sentence does not swallow a question), then forget, then remember.
    """
    raw = text or ""
    if _LIST.match(raw):
        return MemoryOp(kind="list")
    m = _FORGET.match(raw)
    if m:
        payload = _clean(m.group("match"))
        return MemoryOp(kind="forget", payload=payload) if payload else None
    m = _REMEMBER.match(raw)
    if m:
        payload = _normalize_fact(_clean(m.group("fact")))
        return MemoryOp(kind="remember", payload=payload) if payload else None
    return None


def _normalize_fact(fact: str) -> str:
    """Light tidy of a remembered fact: drop a trailing period, keep it as the
    user phrased it (the store dedupes case-insensitively). Empty stays empty."""
    fact = fact.strip()
    if fact.endswith((".", "!")):
        fact = fact[:-1].rstrip()
    return fact
