from __future__ import annotations

from ronin_memory import InMemoryBackend, LongTermMemory, MemoryRecord, SqliteBackend


def test_remember_and_recall_basic() -> None:
    mem = LongTermMemory(backend=InMemoryBackend())
    rid = mem.remember("user prefers dark mode and concise responses", source="onboarding")
    assert rid

    hits = mem.recall("dark mode preference")
    assert hits and hits[0].text.startswith("user prefers dark mode")
    assert hits[0].metadata["source"] == "onboarding"
    assert hits[0].score and hits[0].score > 0


def test_namespace_isolation() -> None:
    mem = LongTermMemory()
    mem.remember("alice likes cats", namespace="alice")
    mem.remember("bob likes dogs", namespace="bob")

    alice_hits = mem.recall("animals", namespace="alice")
    bob_hits = mem.recall("animals", namespace="bob")
    assert all("alice" in h.text for h in alice_hits)
    assert all("bob" in h.text for h in bob_hits)


def test_recall_ranks_and_caps_k() -> None:
    mem = LongTermMemory()
    mem.remember("apple banana cherry")
    mem.remember("banana cherry date")
    mem.remember("totally unrelated text")

    hits = mem.recall("banana cherry", k=2)
    assert len(hits) == 2
    assert hits[0].score >= hits[1].score


def test_forget() -> None:
    mem = LongTermMemory()
    rid = mem.remember("forget me")
    assert mem.forget(rid) is True
    assert mem.recall("forget") == []
    assert mem.forget(rid) is False  # idempotent


def test_sqlite_backend_persists_across_instances(tmp_path) -> None:
    db = tmp_path / "memory.db"
    mem = LongTermMemory(backend=SqliteBackend(db))
    rid = mem.remember("user prefers dark mode", namespace="alice", source="onboarding")

    reopened = LongTermMemory(backend=SqliteBackend(db))
    hits = reopened.recall("dark mode", namespace="alice")

    assert [h.id for h in hits] == [rid]
    assert hits[0].metadata == {"source": "onboarding"}
    assert hits[0].score and hits[0].score > 0


def test_sqlite_backend_namespace_isolation_and_delete(tmp_path) -> None:
    db = tmp_path / "memory.db"
    backend = SqliteBackend(db)
    mem = LongTermMemory(backend=backend)
    alice = mem.remember("alice likes terse answers", namespace="alice")
    mem.remember("bob likes verbose answers", namespace="bob")

    assert mem.recall("answers", namespace="alice")[0].id == alice
    assert mem.forget(alice, namespace="alice") is True
    assert mem.recall("answers", namespace="alice") == []
    assert mem.recall("answers", namespace="bob")


def test_sqlite_backend_can_use_custom_scorer(tmp_path) -> None:
    db = tmp_path / "memory.db"

    def scorer(query: str, candidate: str) -> float:
        return 1.0 if candidate.endswith(query) else 0.0

    mem = LongTermMemory(backend=SqliteBackend(db, score_fn=scorer))
    mem.remember("first alpha")
    mem.remember("second beta")

    assert [h.text for h in mem.recall("beta")] == ["second beta"]


def test_sqlite_backend_prunes_oldest_records_per_namespace(tmp_path) -> None:
    db = tmp_path / "memory.db"
    backend = SqliteBackend(db, max_records_per_namespace=2)
    backend.upsert(MemoryRecord(id="one", text="one shared"))
    backend.upsert(MemoryRecord(id="two", text="two shared"))
    backend.upsert(MemoryRecord(id="three", text="three shared"))

    hits = LongTermMemory(backend=backend).recall("shared", k=10)
    assert [h.id for h in hits] == ["three", "two"]
