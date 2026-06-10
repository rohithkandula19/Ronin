from __future__ import annotations

from ronin_memory import InMemoryBackend, LongTermMemory


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
