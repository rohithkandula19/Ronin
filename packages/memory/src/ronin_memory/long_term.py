from __future__ import annotations

import uuid
from typing import Any, Protocol

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
