"""Session search — a real full-text index over your past conversations.

Every session is archived to ``.ronin/sessions/<id>.json`` (see ``sessions.py``).
This module builds a **SQLite FTS5** index over those transcripts at
``~/.ronin/sessions.db`` and searches it, so ``ronin search "auth bug"`` finds
the conversation where you fixed it and shows a snippet — across every project,
or scoped to the current repo.

Why a real index (not just the in-memory ranker in ``recall.py``): the archive
grows without bound, and re-reading + re-scoring every transcript on each query
is wasteful. The FTS index is incremental (only changed sessions are re-indexed,
tracked by a content hash) and ranked by BM25. If SQLite was built without FTS5,
we fall back to the pure ranker in ``recall.py`` so search still works — just
without the index.

Offline and dependency-free: ``sqlite3`` is in the stdlib. No provider, no
network.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import sessions as _sessions
from .recall import Hit, best_snippet, rank, tokenize


def _db_path() -> Path:
    return Path.home() / ".ronin" / "sessions.db"


def fts5_available() -> bool:
    """True iff this SQLite build supports FTS5 (so the index can be used)."""
    try:
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
            return True
        finally:
            con.close()
    except sqlite3.Error:
        return False


def _transcript_hash(transcript: list[str]) -> str:
    h = hashlib.sha1()
    for entry in transcript:
        h.update(entry.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    # docs: one row per session with its metadata + content hash (so we can skip
    # unchanged sessions on reindex). fts: the searchable body, kept in sync.
    con.execute(
        "CREATE TABLE IF NOT EXISTS docs ("
        "  session_id TEXT PRIMARY KEY,"
        "  root TEXT, proj TEXT, title TEXT, updated TEXT, hash TEXT"
        ")"
    )
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5("
        "  session_id UNINDEXED, proj UNINDEXED, title, body,"
        "  tokenize = 'porter unicode61'"
        ")"
    )
    return con


def _index_one(con: sqlite3.Connection, meta: dict, transcript: list[str]) -> bool:
    """Upsert one session into the index. Returns True if it was (re)written,
    False if it was already up to date (hash unchanged)."""
    sid = str(meta.get("id", ""))
    if not sid or not transcript:
        return False
    h = _transcript_hash(transcript)
    row = con.execute("SELECT hash FROM docs WHERE session_id = ?", (sid,)).fetchone()
    if row is not None and row[0] == h:
        return False  # unchanged — skip
    body = "\n".join(transcript)
    title = meta.get("title", "")
    proj = meta.get("proj") or ""
    if not proj and meta.get("root"):
        proj = _sessions._proj_key(meta["root"])
    # remove any stale copy, then insert fresh (keeps fts + docs consistent)
    con.execute("DELETE FROM fts WHERE session_id = ?", (sid,))
    con.execute("DELETE FROM docs WHERE session_id = ?", (sid,))
    con.execute(
        "INSERT INTO fts (session_id, proj, title, body) VALUES (?, ?, ?, ?)",
        (sid, proj, title, body),
    )
    con.execute(
        "INSERT INTO docs (session_id, root, proj, title, updated, hash) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (sid, meta.get("root", ""), proj, title, meta.get("updated", ""), h),
    )
    return True


def reindex(root: Path | str | None = None) -> int:
    """Index every archived session (or only ``root``'s) into the FTS database.

    Incremental: sessions whose transcript hash is unchanged are skipped, and
    sessions that no longer exist on disk are pruned. Returns the number of
    sessions (re)written this pass. No-op (returns 0) if FTS5 is unavailable.
    """
    if not fts5_available():
        return 0
    metas = _sessions.list_sessions(root)
    con = _connect()
    written = 0
    try:
        live_ids: set[str] = set()
        for meta in metas:
            sid = str(meta.get("id", ""))
            transcript = _sessions.load_session(sid)
            if not transcript:
                continue
            live_ids.add(sid)
            if _index_one(con, meta, transcript):
                written += 1
        # prune sessions deleted from disk (only when reindexing the whole store)
        if root is None and live_ids:
            existing = {r[0] for r in con.execute("SELECT session_id FROM docs").fetchall()}
            for stale in existing - live_ids:
                con.execute("DELETE FROM fts WHERE session_id = ?", (stale,))
                con.execute("DELETE FROM docs WHERE session_id = ?", (stale,))
        con.commit()
    finally:
        con.close()
    return written


def _fts_query(terms: list[str]) -> str:
    """Build a safe FTS5 MATCH expression: OR the terms together, each quoted so
    punctuation can't inject FTS operators. Empty if no terms."""
    quoted = ['"' + t.replace('"', '""') + '"' for t in terms]
    return " OR ".join(quoted)


def search(query: str, *, root: Path | str | None = None, limit: int = 8) -> list[Hit]:
    """Search indexed sessions for ``query``; return ranked ``Hit``s with
    snippets. Scoped to ``root`` (this repo) when given, else all sessions.

    Reindexes first so freshly archived sessions are searchable immediately. If
    FTS5 is unavailable or the query errors, falls back to the pure ranker over
    the JSON transcripts (``recall.rank``)."""
    terms = tokenize(query)
    if not terms:
        return []

    if not fts5_available():
        return _fallback(query, root, limit)

    reindex(root)
    proj = _sessions._proj_key(root) if root is not None else None
    con = _connect()
    try:
        match = _fts_query(terms)
        sql = (
            "SELECT session_id, title, "
            "  snippet(fts, 3, '', '', ' … ', 12) AS snip, "
            "  bm25(fts) AS score "
            "FROM fts WHERE fts MATCH ?"
        )
        params: list = [match]
        if proj is not None:
            sql += " AND proj = ?"
            params.append(proj)
        sql += " ORDER BY score LIMIT ?"  # bm25 is lower = better
        params.append(limit)
        rows = con.execute(sql, params).fetchall()
    except sqlite3.Error:
        con.close()
        return _fallback(query, root, limit)
    finally:
        try:
            con.close()
        except sqlite3.Error:
            pass

    hits: list[Hit] = []
    for sid, title, snip, score in rows:
        snippet = (snip or "").strip().replace("\n", " ")
        if not snippet:
            # fall back to a hand-rolled snippet from the transcript
            snippet = best_snippet(_sessions.load_session(sid), terms)
        # bm25 returns negative scores (more negative = more relevant); expose a
        # positive, monotonically-better integer so Hit.score stays comparable.
        hits.append(Hit(session_id=str(sid), title=title or "",
                        score=int(round(-(score or 0.0) * 100)), snippet=snippet))
    return hits


def _fallback(query: str, root: Path | str | None, limit: int) -> list[Hit]:
    """Pure-Python search when FTS5 isn't available: rank the JSON transcripts."""
    metas = _sessions.list_sessions(root)
    items = [(m, _sessions.load_session(str(m.get("id", "")))) for m in metas]
    return rank(items, query, limit=limit)
