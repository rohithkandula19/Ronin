"""Tests for parallel Nightshift execution + ordering + done-marking."""
from __future__ import annotations

from pathlib import Path

import ronin_cli.nightshift as ns
import ronin_cli.worktree as wt
from ronin_cli.nightshift import Task, TaskResult


class _NullConsole:
    def print(self, *a, **k):
        pass


def _tasks(n: int) -> list[Task]:
    return [Task("todo", f"t{i}", "do it", ref=f"a.py:{i}") for i in range(n)]


def test_parallel_collects_in_order(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(wt, "is_git_repo", lambda r: True)

    def fake_work(config, root, task, index, **kw):
        return TaskResult(task, "patched", note="ok", patch_path=f"/p/{index}.patch"), 0.0
    monkeypatch.setattr(ns, "_work_one", fake_work)

    results = ns.run_nightshift(None, tmp_path, _NullConsole(), _tasks(6),
                                execute=True, parallel=3)
    assert len(results) == 6
    # results are reassembled in submission order despite concurrent completion
    assert [r.task.title for r in results] == [f"t{i}" for i in range(6)]
    # every shipped task is recorded done (race-free, post-run)
    assert len(ns.load_done(tmp_path)) == 6


def test_parallel_mixed_statuses(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(wt, "is_git_repo", lambda r: True)

    def fake_work(config, root, task, index, **kw):
        status = "patched" if index % 2 else "failed-tests"
        return TaskResult(task, status, note="x", patch_path="/p" if index % 2 else ""), 0.0
    monkeypatch.setattr(ns, "_work_one", fake_work)

    results = ns.run_nightshift(None, tmp_path, _NullConsole(), _tasks(4),
                                execute=True, parallel=4)
    s = ns.summarize(results)
    assert s["patched"] == 2 and s["failed-tests"] == 2


def test_budget_forces_sequential(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(wt, "is_git_repo", lambda r: True)
    calls = []

    def fake_work(config, root, task, index, **kw):
        calls.append(index)
        return TaskResult(task, "patched", note="ok", patch_path=f"/p/{index}"), 0.40
    monkeypatch.setattr(ns, "_work_one", fake_work)

    # budget 1.00, each task 'costs' 0.40 → after 3 tasks (1.20) the rest are skipped
    results = ns.run_nightshift(None, tmp_path, _NullConsole(), _tasks(6),
                                execute=True, parallel=5, budget=1.00)
    statuses = [r.status for r in results]
    assert "skipped-budget" in statuses               # budget enforced (sequential)
    assert statuses.count("patched") <= 3


def test_dry_run_unaffected(tmp_path: Path) -> None:
    results = ns.run_nightshift(None, tmp_path, _NullConsole(), _tasks(3),
                                execute=False, parallel=4)
    assert all(r.status == "skipped-budget" for r in results)
