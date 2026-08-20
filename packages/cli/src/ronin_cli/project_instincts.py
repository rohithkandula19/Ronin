"""Evidence-backed, local project practices for agents to recall safely."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .local_embed import cosine, hashing_embed
from .project_memory import _MAX_TEXT, _SECRET


_ACTIVE_CONFIDENCE = 0.70


class ProjectInstinctStore:
    """Keep candidate practices separate from maintainer instructions and facts."""

    def __init__(self, root: Path | str) -> None:
        self.path = Path(root).resolve() / ".ronin" / "project-memory.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def propose(
        self,
        text: str,
        *,
        evidence: str,
        confidence: float = 0.35,
        expires_in_days: int = 30,
    ) -> str:
        clean = _validate_text(text)
        evidence_clean = _validate_text(evidence, label="evidence")
        confidence = _clamp(confidence)
        now = time.time()
        expires_at = now + max(1, min(expires_in_days, 365)) * 86_400
        state = "active" if confidence >= _ACTIVE_CONFIDENCE else "candidate"
        instinct_id = str(uuid.uuid4())
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO project_instincts(id, text, confidence, observations, state,
                                              evidence_json, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (instinct_id, clean, confidence, state, json.dumps([evidence_clean]), now, now, expires_at),
            )
        return instinct_id

    def reinforce(self, instinct_id: str, *, evidence: str, weight: float = 0.15) -> dict[str, Any]:
        evidence_clean = _validate_text(evidence, label="evidence")
        with self._connect() as con:
            row = con.execute("SELECT * FROM project_instincts WHERE id = ?", (instinct_id,)).fetchone()
            if row is None:
                raise KeyError(instinct_id)
            if float(row["expires_at"]) <= time.time():
                raise ValueError("instinct has expired; propose a fresh candidate instead")
            evidence_items = _evidence(row["evidence_json"])
            if evidence_clean not in evidence_items:
                evidence_items.append(evidence_clean)
            confidence = _clamp(float(row["confidence"]) + max(0.01, min(weight, 0.5)))
            state = "active" if confidence >= _ACTIVE_CONFIDENCE else "candidate"
            now = time.time()
            con.execute(
                """
                UPDATE project_instincts
                SET confidence = ?, observations = observations + 1, state = ?,
                    evidence_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (confidence, state, json.dumps(evidence_items), now, instinct_id),
            )
        return self.get(instinct_id)

    def get(self, instinct_id: str) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute("SELECT * FROM project_instincts WHERE id = ?", (instinct_id,)).fetchone()
        if row is None:
            raise KeyError(instinct_id)
        return _row(row)

    def list(self, *, state: str | None = None, include_expired: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM project_instincts"
        values: list[Any] = []
        clauses: list[str] = []
        if state is not None:
            clauses.append("state = ?")
            values.append(state)
        if not include_expired:
            clauses.append("expires_at > ?")
            values.append(time.time())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY confidence DESC, updated_at DESC, id ASC"
        with self._connect() as con:
            rows = con.execute(query, values).fetchall()
        return [_row(row) for row in rows]

    def recall(self, query: str, *, k: int = 5) -> list[dict[str, Any]]:
        candidate = query.strip()
        if not candidate:
            return []
        scored: list[dict[str, Any]] = []
        for instinct in self.list(state="active"):
            similarity = max(0.0, cosine(hashing_embed(candidate), hashing_embed(instinct["text"])))
            if similarity <= 0:
                continue
            instinct["score"] = round(similarity * float(instinct["confidence"]), 4)
            scored.append(instinct)
        scored.sort(key=lambda item: (item["score"], item["updated_at"], item["id"]), reverse=True)
        return scored[: max(1, min(k, 20))]

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS project_instincts (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    observations INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('candidate', 'active')),
                    evidence_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_instincts_active
                ON project_instincts(state, expires_at, confidence DESC, updated_at DESC)
                """
            )


def _validate_text(value: str, *, label: str = "instinct") -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{label} text must not be empty")
    if len(clean) > _MAX_TEXT:
        raise ValueError(f"{label} entries are limited to 8,000 characters")
    if _SECRET.search(clean):
        raise ValueError(f"refusing to store a possible secret in project {label}")
    return clean


def _clamp(value: float) -> float:
    return round(max(0.0, min(float(value), 1.0)), 4)


def _evidence(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "text": row["text"],
        "confidence": round(float(row["confidence"]), 4),
        "observations": int(row["observations"]),
        "state": row["state"],
        "evidence": _evidence(row["evidence_json"]),
        "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
        "expires_at": float(row["expires_at"]),
    }
