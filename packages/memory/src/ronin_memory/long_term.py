from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
import uuid
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field


class MemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    namespace: str = "default"
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float | None = None


class LongTermBackend(Protocol):
    """Storage interface. Swap ``InMemoryBackend`` for ChromaDB / Pinecone / Weaviate in prod."""

    def upsert(self, record: MemoryRecord) -> None: ...

    def query(self, namespace: str, text: str, k: int = 5) -> list[MemoryRecord]: ...

    def delete(self, namespace: str, record_id: str) -> bool: ...


def _jaccard(a: str, b: str) -> float:
    """Simple word-set Jaccard. Fine for dev/test; replace with vector similarity in prod."""
    aw = {w for w in a.lower().split() if len(w) > 1}
    bw = {w for w in b.lower().split() if len(w) > 1}
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / len(aw | bw)


class InMemoryBackend:
    """Naive in-process backend. Stores records in a dict, ranks by Jaccard overlap.

    Useful for development and tests. NOT a substitute for a real vector store at scale.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], MemoryRecord] = {}

    def upsert(self, record: MemoryRecord) -> None:
        self._records[(record.namespace, record.id)] = record

    def query(self, namespace: str, text: str, k: int = 5) -> list[MemoryRecord]:
        scored: list[MemoryRecord] = []
        for (ns, _), rec in self._records.items():
            if ns != namespace:
                continue
            score = _jaccard(text, rec.text)
            if score > 0:
                copy = rec.model_copy(update={"score": round(score, 4)})
                scored.append(copy)
        scored.sort(key=lambda r: r.score or 0, reverse=True)
        return scored[:k]

    def delete(self, namespace: str, record_id: str) -> bool:
        return self._records.pop((namespace, record_id), None) is not None


ScoreFn = Callable[[str, str], float]


class SqliteBackend:
    """Persistent local backend with deterministic lexical retrieval.

    Records are stored in one SQLite file and scoped by namespace. Querying is
    intentionally local and dependency-free: by default it uses the same Jaccard
    scorer as ``InMemoryBackend``. Callers that have embeddings or a stronger
    ranker can pass ``score_fn`` without changing the ``LongTermMemory`` API.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        score_fn: ScoreFn | None = None,
        max_records_per_namespace: int | None = None,
    ) -> None:
        self.path = Path(path).expanduser()
        self.score_fn = score_fn or _jaccard
        self.max_records_per_namespace = max_records_per_namespace
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    namespace TEXT NOT NULL,
                    id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (namespace, id)
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_namespace_updated "
                "ON memories(namespace, updated_at DESC)"
            )

    def upsert(self, record: MemoryRecord) -> None:
        now = time.time()
        metadata_json = json.dumps(record.metadata, sort_keys=True, separators=(",", ":"))
        with self._connect() as con:
            existing = con.execute(
                "SELECT created_at FROM memories WHERE namespace = ? AND id = ?",
                (record.namespace, record.id),
            ).fetchone()
            created_at = float(existing["created_at"]) if existing else now
            con.execute(
                """
                INSERT INTO memories(namespace, id, text, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, id) DO UPDATE SET
                    text = excluded.text,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (record.namespace, record.id, record.text, metadata_json, created_at, now),
            )
            self._prune_namespace(con, record.namespace)

    def query(self, namespace: str, text: str, k: int = 5) -> list[MemoryRecord]:
        if k <= 0:
            return []
        scored: list[tuple[float, float, MemoryRecord]] = []
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, text, metadata_json, updated_at FROM memories WHERE namespace = ?",
                (namespace,),
            ).fetchall()
        for row in rows:
            score = self.score_fn(text, row["text"])
            if score <= 0:
                continue
            try:
                metadata = json.loads(row["metadata_json"])
            except json.JSONDecodeError:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            record = MemoryRecord(
                id=row["id"],
                namespace=namespace,
                text=row["text"],
                metadata=metadata,
                score=round(float(score), 4),
            )
            scored.append((float(score), float(row["updated_at"]), record))
        scored.sort(key=lambda item: (item[0], item[1], item[2].id), reverse=True)
        return [record for _, _, record in scored[:k]]

    def delete(self, namespace: str, record_id: str) -> bool:
        with self._connect() as con:
            cur = con.execute(
                "DELETE FROM memories WHERE namespace = ? AND id = ?",
                (namespace, record_id),
            )
            return cur.rowcount > 0

    def _prune_namespace(self, con: sqlite3.Connection, namespace: str) -> None:
        if self.max_records_per_namespace is None:
            return
        limit = max(int(self.max_records_per_namespace), 0)
        con.execute(
            """
            DELETE FROM memories
            WHERE namespace = ?
              AND id NOT IN (
                SELECT id FROM memories
                WHERE namespace = ?
                ORDER BY updated_at DESC
                LIMIT ?
              )
            """,
            (namespace, namespace, limit),
        )


class LongTermMemory(BaseModel):
    """Long-term memory wrapper. User-scope via ``namespace``.

    Pluggable backend — swap in ChromaDB by writing a class that satisfies ``LongTermBackend``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    backend: Any = Field(default_factory=InMemoryBackend)

    def remember(self, text: str, namespace: str = "default", **metadata: Any) -> str:
        record = MemoryRecord(namespace=namespace, text=text, metadata=metadata)
        self.backend.upsert(record)
        return record.id

    def recall(self, query: str, namespace: str = "default", k: int = 5) -> list[MemoryRecord]:
        return self.backend.query(namespace, query, k=k)

    def forget(self, record_id: str, namespace: str = "default") -> bool:
        return self.backend.delete(namespace, record_id)
