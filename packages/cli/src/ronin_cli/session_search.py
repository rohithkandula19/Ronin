"""Searchable session memory — ``ronin recall "<query>"`` over past sessions.

ronin archives every session under ``.ronin/sessions/<id>.json`` but, until you
index them, that archive is dead weight. This module turns it into a *local*,
PII-redacted, full-text search index — like Hermes's FTS5 session search, but it
never leaves the machine and every transcript is scrubbed of secrets *before* a
single character is written to the index.

Design notes
------------
- **Local-only.** The index lives at ``<RONIN_HOME or ~/.ronin>/recall.db`` (the
  same home that memory/runs honour). It is never uploaded, and ``recall()`` is
  read-only.
- **Redact-on-index.** Every transcript is passed through :func:`redact_for_index`
  (which reuses the shared :mod:`ronin_cli.redact` helper and then masks any
  short ``sk-`` / ``rk_`` / ``xoxb-`` token or email the length-gated patterns
  miss) so secrets never enter the database.
- **FTS5 with graceful fallback.** We try SQLite's ``fts5`` virtual table for
  fast ranked search; if this Python wasn't built with FTS5, we transparently
  fall back to a plain table scanned with a pure-Python BM25-ish ranker.
- **Pure, testable core.** :func:`tokenize`, :func:`rank`, and
  :func:`redact_for_index` have no I/O and are unit-tested directly.

Nothing here needs a network connection or a model.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

# Reuse ronin's shared redactor so the index masks the same secret/PII classes
# (JWTs, AWS/GitHub/Slack/Stripe/OpenAI keys, bearer tokens, emails, IPs) the
# rest of the tool already knows about — one source of truth for "what is secret".
from .redact import redact_text

# --------------------------------------------------------------------------- #
# Pure core: tokenize, rank, redact_for_index
# --------------------------------------------------------------------------- #

# Tiny stop list — these carry no signal for matching a query to a transcript,
# and dropping them keeps a query like "add retry to the http client" from being
# dominated by "to"/"the". Kept deliberately small; recall over code chat wants
# to preserve almost everything (identifiers, verbs, nouns).
_STOPWORDS = frozenset(
    "a an the and or of to in on at for with is are am be do does did this that "
    "it its as by from".split()
)

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    """Lowercased word/identifier tokens of ``text`` (stop-words dropped).

    Order-preserving but **not** deduped — term frequency matters for ranking.
    Single characters are dropped (no signal). Pure."""
    out: list[str] = []
    for w in _WORD_RE.findall((text or "").lower()):
        if len(w) > 1 and w not in _STOPWORDS:
            out.append(w)
    return out


def rank(query: str, doc: str) -> float:
    """Relevance of ``doc`` to ``query`` — higher means a better match.

    A compact BM25-style score over a single document: each query term that
    appears in the doc contributes a saturating term-frequency component (so the
    50th occurrence of a word adds little) and an inverse-length normalisation
    (so a short, focused doc isn't out-ranked by a long rambling one purely on
    raw counts). Returns ``0.0`` when no query term occurs. Pure and
    dependency-free, so a matching doc is guaranteed to out-score a non-matching
    one."""
    q_terms = tokenize(query)
    if not q_terms:
        return 0.0
    d_tokens = tokenize(doc)
    if not d_tokens:
        return 0.0

    # Term frequencies in the document.
    tf: dict[str, int] = {}
    for t in d_tokens:
        tf[t] = tf.get(t, 0) + 1

    doc_len = len(d_tokens)
    # BM25 constants (with no corpus we use a fixed average length so the formula
    # stays well-behaved on a single doc): k1 saturates TF, b controls length
    # normalisation.
    k1, b, avg_len = 1.5, 0.75, 60.0
    norm = k1 * (1.0 - b + b * (doc_len / avg_len))

    score = 0.0
    seen_q: set[str] = set()
    for term in q_terms:
        f = tf.get(term, 0)
        if f <= 0:
            continue
        # Saturating term frequency (the BM25 TF kernel).
        score += (f * (k1 + 1.0)) / (f + norm)
        # Small bonus the first time a *distinct* query term is matched, so a doc
        # covering more of the query beats one that merely repeats a single term.
        if term not in seen_q:
            score += 0.5
            seen_q.add(term)
    return score


# Catch-all masks for secrets the length-gated shared patterns can miss (e.g. a
# short ``sk-abc123`` planted in a test, or a truncated ``xoxb-`` token). These
# run *after* redact_text() as a belt-and-suspenders pass so nothing prefixed
# like a known key class can slip into the index.
_EXTRA_SECRET_PATTERNS: list[tuple[str, str]] = [
    # OpenAI-style and Stripe-style keys of ANY length after the prefix.
    ("KEY", r"\bsk-[A-Za-z0-9_-]+"),
    ("KEY", r"\b[sr]k_[A-Za-z0-9_-]+"),
    # Slack tokens of any length.
    ("KEY", r"\bxox[baprs]-[A-Za-z0-9-]+"),
    # Any leftover emails (defensive; redact_text already handles standard ones).
    ("EMAIL", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
]


def redact_for_index(text: str) -> str:
    """Scrub secrets/PII from ``text`` so they never enter the search index.

    Reuses the shared :func:`ronin_cli.redact.redact_text` (JWTs, AWS/GitHub/
    Slack/Stripe/OpenAI keys, bearer tokens, emails, IPs) and then applies an
    extra aggressive pass that masks any ``sk-`` / ``rk_`` / ``sk_`` / ``xoxb-``
    token or email regardless of length — the shared patterns are length-gated
    and would let a short key slip through. Returns the redacted string. Pure."""
    out = redact_text(text or "").get("text", text or "")
    for label, pat in _EXTRA_SECRET_PATTERNS:
        out = re.sub(pat, f"[REDACTED-{label}]", out)
    return out


# --------------------------------------------------------------------------- #
# Storage location (local-only, honours RONIN_HOME)
# --------------------------------------------------------------------------- #

def default_db_path() -> Path:
    """Location of the recall index. Honours ``RONIN_HOME`` (the same override
    memory/runs use), defaulting to ``~/.ronin/recall.db``. The index is
    user-global and **never leaves the machine**."""
    home = os.environ.get("RONIN_HOME")
    base = Path(home) if home else Path.home() / ".ronin"
    return base / "recall.db"


def default_session_dir() -> Path:
    """Where ronin archives sessions: ``.ronin/sessions`` in the current repo."""
    return Path(".ronin") / "sessions"


# --------------------------------------------------------------------------- #
# SQLite layer: FTS5 when available, plain-table fallback otherwise
# --------------------------------------------------------------------------- #

def _fts5_available(conn: sqlite3.Connection) -> bool:
    """True if this SQLite was compiled with the FTS5 extension."""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def _connect(db_path: Path | str) -> tuple[sqlite3.Connection, bool]:
    """Open (creating dirs/tables as needed) the index DB. Returns the connection
    and whether FTS5 is in use. The schema is chosen once at creation and stored
    in a ``meta`` table so reads pick the matching query path."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row

    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    row = conn.execute("SELECT value FROM meta WHERE key='engine'").fetchone()
    if row is not None:
        use_fts = row["value"] == "fts5"
    else:
        use_fts = _fts5_available(conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('engine', ?)",
            ("fts5" if use_fts else "like",),
        )

    if use_fts:
        # Contentless-ish FTS table; we keep all columns in the FTS table itself
        # so a single index serves both search and snippet rendering. session_id,
        # when, and root are UNINDEXED (metadata, not searched).
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS sessions USING fts5("
            "session_id UNINDEXED, when_ts UNINDEXED, title, body, "
            "root UNINDEXED, tokenize='unicode61')"
        )
    else:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "session_id TEXT PRIMARY KEY, when_ts TEXT, title TEXT, "
            "body TEXT, root TEXT)"
        )
    conn.commit()
    return conn, use_fts


def _iter_session_files(session_dir: Path) -> list[Path]:
    if not session_dir.is_dir():
        return []
    return sorted(session_dir.glob("*.json"))


def _load_transcript(data: dict) -> tuple[str, str, str, str]:
    """Pull (session_id, when, title, body) out of a session JSON dict.

    ``body`` is the full transcript joined with newlines — redaction happens in
    the caller so this stays a pure shape-extractor."""
    sid = str(data.get("id", ""))
    when = str(data.get("updated", ""))
    title = str(data.get("title", ""))
    transcript = data.get("transcript", [])
    if isinstance(transcript, list):
        body = "\n".join(str(e) for e in transcript)
    else:
        body = str(transcript)
    return sid, when, title, body


def index_sessions(session_dir: Path | str, db_path: Path | str) -> int:
    """Read every archived session under ``session_dir``, redact it, and insert
    it into the local index at ``db_path``. Returns the number of sessions
    indexed.

    Re-running is idempotent: an existing row for the same ``session_id`` is
    replaced, so re-indexing after new sessions land keeps the index fresh
    without duplicates. Every body and title is passed through
    :func:`redact_for_index` *before* it is written, so secrets never reach
    disk."""
    session_dir = Path(session_dir)
    conn, use_fts = _connect(db_path)
    count = 0
    try:
        for f in _iter_session_files(session_dir):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            sid, when, title, body = _load_transcript(data)
            if not sid:
                sid = f.stem
            # REDACT before indexing — the whole point of the local index.
            title = redact_for_index(title)
            body = redact_for_index(body)

            # Replace any existing row for this session so re-indexing is clean.
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (sid,))
            conn.execute(
                "INSERT INTO sessions(session_id, when_ts, title, body, root) "
                "VALUES(?, ?, ?, ?, ?)",
                (sid, when, title, body, str(data.get("root", ""))),
            )
            count += 1
        conn.commit()
    finally:
        conn.close()
    return count


def _make_snippet(body: str, terms: list[str], *, width: int = 160) -> str:
    """A short excerpt of ``body`` centred on the first matching term, newlines
    flattened. Falls back to the head of the body if nothing matches."""
    low = body.lower()
    pos = -1
    for t in terms:
        i = low.find(t)
        if i >= 0 and (pos < 0 or i < pos):
            pos = i
    if pos < 0:
        snip = body[:width]
        prefix = ""
    else:
        start = max(0, pos - width // 3)
        snip = body[start:start + width]
        prefix = "…" if start > 0 else ""
    snip = " ".join(snip.split())
    suffix = "…" if len(body) > len(snip) + 1 else ""
    return f"{prefix}{snip}{suffix}".strip()


def _fts_match_query(terms: list[str]) -> str:
    """Build a safe FTS5 MATCH expression from already-tokenized terms.

    Each term is quoted (so punctuation/operators can't break the query) and
    OR-combined, mirroring the OR semantics of the fallback ranker — a session
    matching ANY query term is a candidate, then ranked."""
    return " OR ".join(f'"{t}"' for t in terms)


def _score_rows(db_rows: list[Any], query: str, terms: list[str],
                limit: int) -> list[dict[str, Any]]:
    """Score candidate DB rows with the pure :func:`rank`, drop non-matches, and
    return the top ``limit`` as hit dicts, best first. Shared by both the FTS5
    and plain-table paths so scoring is identical regardless of engine."""
    scored: list[tuple[float, dict[str, Any]]] = []
    for r in db_rows:
        body = r["body"] or ""
        title = r["title"] or ""
        sc = rank(query, f"{title}\n{body}")
        if sc <= 0:
            continue
        scored.append((sc, {
            "session_id": r["session_id"],
            "when": r["when_ts"] or "",
            "snippet": _make_snippet(body, terms) or title,
            "score": round(sc, 4),
        }))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [d for _, d in scored[:limit]]


def recall(query: str, db_path: Path | str, *, limit: int = 8) -> list[dict[str, Any]]:
    """Search the local index for sessions matching ``query``.

    Returns up to ``limit`` ranked hits, best first, each a dict::

        {"session_id": str, "when": str, "snippet": str, "score": float}

    Read-only: it never writes to the index. Uses FTS5's ``bm25()`` when the
    index was built with FTS5, otherwise scans the plain table and ranks each
    row with the pure :func:`rank`. Returns ``[]`` for an empty query, a missing
    DB, or no matches."""
    terms = tokenize(query)
    if not terms:
        return []
    p = Path(db_path)
    if not p.exists():
        return []

    conn, use_fts = _connect(p)
    try:
        rows: list[dict[str, Any]] = []
        if use_fts:
            try:
                match = _fts_match_query(terms)
                # FTS5 does the heavy lifting of *narrowing* to candidate rows
                # fast (and its bm25() pre-orders them). But bm25's magnitude is
                # unstable on a tiny corpus (it can return 0.0 even for a real
                # match), so we re-score the candidates with our own pure rank()
                # — guaranteed positive for a hit and identical to the fallback
                # path, giving one interpretable, stable scoring story.
                cur = conn.execute(
                    "SELECT session_id, when_ts, title, body "
                    "FROM sessions WHERE sessions MATCH ? "
                    "ORDER BY bm25(sessions)",
                    (match,),
                )
                rows = _score_rows(cur.fetchall(), query, terms, limit)
            except sqlite3.OperationalError:
                # Corrupt/legacy FTS state — fall through to a manual scan.
                use_fts = False
                rows = []

        if not use_fts:
            cur = conn.execute(
                "SELECT session_id, when_ts, title, body FROM sessions"
            )
            rows = _score_rows(cur.fetchall(), query, terms, limit)
        return rows
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Rendering (teal #2dd4bf accents, mute #6b7089)
# --------------------------------------------------------------------------- #

def render_recall(console: Any, hits: list[dict[str, Any]]) -> None:
    """Pretty-print recall ``hits`` to a rich ``console`` — teal accents (#2dd4bf)
    and muted slate (#6b7089) for metadata, matching ronin's palette."""
    if not hits:
        console.print("[#6b7089]no matching sessions found.[/#6b7089]")
        return
    n = len(hits)
    console.print(f"[#2dd4bf]{n} match{'es' if n != 1 else ''}[/#2dd4bf] "
                  f"[#6b7089]in your past sessions[/#6b7089]")
    for h in hits:
        sid = str(h.get("session_id", "?"))[:14]
        when = str(h.get("when", "")) or "—"
        score = h.get("score", 0)
        console.print(
            f"\n[#2dd4bf]{sid}[/#2dd4bf] "
            f"[#6b7089]· {when} · score {score}[/#6b7089]"
        )
        snippet = h.get("snippet") or ""
        if snippet:
            console.print(f"  [#c0caf5]{snippet}[/#c0caf5]")
    console.print(
        f"\n[#6b7089]replay one with [#2dd4bf]ronin replay <id>[/#2dd4bf].[/#6b7089]"
    )


# --------------------------------------------------------------------------- #
# Convenience: ensure the index is fresh, then search (used by `ronin recall`)
# --------------------------------------------------------------------------- #

def recall_sessions(query: str, *, session_dir: Path | str | None = None,
                    db_path: Path | str | None = None,
                    limit: int = 8, reindex: bool = True) -> list[dict[str, Any]]:
    """High-level helper: (optionally) refresh the local index from the session
    archive, then run :func:`recall`. Defaults to the repo's ``.ronin/sessions``
    and ``~/.ronin/recall.db``. This is the function a ``ronin recall`` command
    would call. Read-only with respect to your sessions; it only writes to the
    local index. No network."""
    sdir = Path(session_dir) if session_dir is not None else default_session_dir()
    db = Path(db_path) if db_path is not None else default_db_path()
    if reindex:
        try:
            index_sessions(sdir, db)
        except sqlite3.Error:
            pass  # a stale index still beats failing the search outright
    return recall(query, db, limit=limit)


__all__ = [
    "tokenize",
    "rank",
    "redact_for_index",
    "index_sessions",
    "recall",
    "render_recall",
    "recall_sessions",
    "default_db_path",
    "default_session_dir",
]
