from __future__ import annotations

import json

import pytest

from ronin_cli.agent_queue import AgentQueue


def test_queue_lifecycle_is_durable_and_terminal(tmp_path) -> None:
    queue = AgentQueue(tmp_path)
    first = queue.enqueue("inspect the retry behavior")
    second = queue.enqueue("add retry tests", write=True)

    claimed = queue.claim_next()
    assert claimed is not None and claimed.id == first.id and claimed.status == "running"
    completed = queue.finish(first.id, success=True, run_id="agent-1")
    assert completed is not None and completed.status == "completed" and completed.run_id == "agent-1"
    assert queue.finish(first.id, success=True) is None

    cancelled = queue.cancel(second.id)
    assert cancelled is not None and cancelled.status == "cancelled"
    assert [job.status for job in queue.list()] == ["completed", "cancelled"]

    raw = json.loads((tmp_path / ".ronin" / "agent-queue.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1


def test_queue_rejects_empty_goal_and_bad_terminal_transition(tmp_path) -> None:
    queue = AgentQueue(tmp_path)
    with pytest.raises(ValueError, match="must not be empty"):
        queue.enqueue("  ")
    job = queue.enqueue("read docs")
    assert queue.cancel("missing") is None
    assert queue.finish(job.id, success=True) is None
