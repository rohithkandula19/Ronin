"""Recall — search your past sessions, so they stop being dead weight.

ronin archives every session but nothing surfaced them. Recall turns that archive
into a searchable memory: keyword-rank every stored transcript against a query
and show the best-matching turn from each hit. A simple, dependency-free ranker
(term frequency, case-insensitive) — good enough to find "that time I fixed the
auth bug". The scoring + snippet selection are pure and unit-tested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


def tokenize(query: str) -> list[str]:
    """Lowercased word tokens of a query (deduped, order-preserving)."""
    seen: list[str] = []
    for w in re.findall(r"\w+", (query or "").lower()):
        if w not in seen:
            seen.append(w)
    return seen


def score_text(text: str, terms: list[str]) -> int:
    """Total occurrences of all ``terms`` in ``text`` (case-insensitive)."""
    low = (text or "").lower()
    return sum(low.count(t) for t in terms)


def best_snippet(transcript: list[str], terms: list[str], *, width: int = 160) -> str:
    """The single transcript entry with the most term hits, trimmed around the
    first match. Empty string if nothing matches."""
    best, best_score = "", 0
    for entry in transcript:
        sc = score_text(entry, terms)
        if sc > best_score:
            best, best_score = entry, sc
    if not best:
        return ""
    low = best.lower()
    pos = min((low.find(t) for t in terms if low.find(t) >= 0), default=0)
    start = max(0, pos - width // 3)
    snip = best[start:start + width].strip().replace("\n", " ")
    return ("…" if start > 0 else "") + snip


@dataclass
class Hit:
    session_id: str
    title: str
    score: int
    snippet: str


def rank(items: list[tuple[dict, list[str]]], query: str, *, limit: int = 8) -> list[Hit]:
    """Rank (meta, transcript) pairs against ``query``. Highest score first;
    zero-score sessions are dropped. Pure."""
    terms = tokenize(query)
    if not terms:
        return []
    hits: list[Hit] = []
    for meta, transcript in items:
        sc = sum(score_text(e, terms) for e in transcript)
        if sc <= 0:
            continue
        hits.append(Hit(session_id=str(meta.get("id", "?")),
                        title=meta.get("title", ""),
                        score=sc,
                        snippet=best_snippet(transcript, terms)))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]
