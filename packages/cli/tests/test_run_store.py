"""The run_store: faithfulness scores and orchestration runs persisted to .ronin/.

These are the on-disk stores the `ronin ui` dashboard reads. The tests prove a
real round-trip (write then read back exactly what was written), ordering
(newest first), and tolerance of an empty/missing store. Pure file I/O, fast.
"""
from __future__ import annotations

from pathlib import Path

from ronin_cli import run_store


# ---------- faithfulness ----------

def test_faithfulness_round_trip(tmp_path: Path) -> None:
    run_store.record_faithfulness(0.9, verdict="grounded", n_sources=2,
                                  answer="a", root=tmp_path)
    run_store.record_faithfulness(0.1, verdict="ungrounded", n_sources=1,
                                  answer="b", hallucinated_symbols=["zap"], root=tmp_path)

    rows = run_store.load_faithfulness(tmp_path)
    assert len(rows) == 2
    # Newest first.
    assert rows[0]["verdict"] == "ungrounded"
    assert rows[0]["score"] == 0.1
    assert rows[0]["hallucinated_symbols"] == ["zap"]
    assert rows[1]["verdict"] == "grounded"
    assert (tmp_path / ".ronin" / "faithfulness.jsonl").is_file()


def test_faithfulness_summary(tmp_path: Path) -> None:
    run_store.record_faithfulness(0.8, verdict="grounded", root=tmp_path)
    run_store.record_faithfulness(0.4, verdict="ungrounded", root=tmp_path)
    run_store.record_faithfulness(0.5, verdict="abstain", abstain=True, root=tmp_path)
    s = run_store.faithfulness_summary(tmp_path)
    assert s["total"] == 3
    assert s["grounded"] == 1
    assert s["ungrounded"] == 1
    assert s["abstain"] == 1
    assert s["mean_score"] is not None


def test_faithfulness_empty(tmp_path: Path) -> None:
    assert run_store.load_faithfulness(tmp_path) == []
    assert run_store.faithfulness_summary(tmp_path)["total"] == 0


def test_faithfulness_tolerates_corrupt_line(tmp_path: Path) -> None:
    run_store.record_faithfulness(0.7, verdict="grounded", root=tmp_path)
    p = tmp_path / ".ronin" / "faithfulness.jsonl"
    with p.open("a", encoding="utf-8") as fh:
        fh.write("{ this is not valid json\n")
    rows = run_store.load_faithfulness(tmp_path)
    assert len(rows) == 1  # the good record survives, the junk line is skipped


# ---------- orchestrations ----------

def test_orchestration_round_trip(tmp_path: Path) -> None:
    run_store.record_orchestration(
        "build a thing",
        success=True,
        plan_subtasks=[{"id": "a", "description": "do a", "assignee": "researcher", "depends_on": []}],
        subtask_results=[{"subtask_id": "a", "assignee": "researcher", "success": True,
                          "output": "found it", "error": None, "iterations": 1}],
        output="done",
        run_id="run-1",
        root=tmp_path,
    )

    listed = run_store.list_orchestrations(tmp_path)
    assert len(listed) == 1
    assert listed[0]["id"] == "run-1"
    assert listed[0]["goal"] == "build a thing"
    assert listed[0]["subtasks"] == 1

    full = run_store.load_orchestration("run-1", tmp_path)
    assert full is not None
    assert full["plan"][0]["assignee"] == "researcher"
    assert full["subtasks"][0]["output"] == "found it"


def test_orchestration_empty(tmp_path: Path) -> None:
    assert run_store.list_orchestrations(tmp_path) == []
    assert run_store.load_orchestration("nope", tmp_path) is None


def test_orchestration_ordering_newest_first(tmp_path: Path) -> None:
    run_store.record_orchestration("first", success=True, run_id="20260101-100000-aaa", root=tmp_path)
    run_store.record_orchestration("second", success=True, run_id="20260101-110000-bbb", root=tmp_path)
    listed = run_store.list_orchestrations(tmp_path)
    assert [o["goal"] for o in listed] == ["second", "first"]
