"""Tests for Nightshift — backlog gathering, plan, report (pure parts)."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.nightshift import (
    Task,
    TaskResult,
    gather_tasks,
    patch_path,
    run_nightshift,
    summarize,
)


def test_gather_goal_and_todos(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1  # TODO: make configurable\n", encoding="utf-8")
    tasks = gather_tasks(tmp_path, goal="add a --json flag")
    sources = [t.source for t in tasks]
    assert "goal" in sources and "todo" in sources
    goal = next(t for t in tasks if t.source == "goal")
    assert goal.title == "add a --json flag"
    todo = next(t for t in tasks if t.source == "todo")
    assert "a.py" in todo.ref


def test_gather_respects_limit(tmp_path: Path) -> None:
    body = "".join(f"# TODO: thing {i}\n" for i in range(30))
    (tmp_path / "m.py").write_text(body, encoding="utf-8")
    assert len(gather_tasks(tmp_path, limit=5)) == 5


def test_gather_empty(tmp_path: Path) -> None:
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    assert gather_tasks(tmp_path) == []


def test_task_prompt_includes_ref() -> None:
    t = Task("todo", "FIXME in a.py", "handle empty input", ref="a.py:3")
    p = t.prompt()
    assert "a.py:3" in p and "handle empty input" in p and "smallest correct change" in p


def test_dry_run_returns_skipped(tmp_path: Path) -> None:
    tasks = [Task("goal", "x", "do x")]
    results = run_nightshift(None, tmp_path, _NullConsole(), tasks, execute=False)
    assert len(results) == 1 and results[0].status == "skipped-budget"


def test_patch_path_is_safe(tmp_path: Path) -> None:
    p = patch_path(tmp_path, 3, Task("todo", "Fix the /weird: thing!", "d"))
    assert p.name.startswith("03-") and p.suffix == ".patch"
    assert "/" not in p.name and ":" not in p.name


def test_summarize_counts() -> None:
    results = [
        TaskResult(Task("todo", "a", "d"), "patched"),
        TaskResult(Task("todo", "b", "d"), "patched"),
        TaskResult(Task("todo", "c", "d"), "failed-tests"),
    ]
    s = summarize(results)
    assert s["patched"] == 2 and s["failed-tests"] == 1 and s["total"] == 3


class _NullConsole:
    def print(self, *a, **k):
        pass
