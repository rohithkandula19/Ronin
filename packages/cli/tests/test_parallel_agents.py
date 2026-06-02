"""Tests for the parallel + worktree-isolated sub-agent tools. ``run_code_agent``
is monkeypatched with a fake so no real LLM/provider calls happen — the tests
cover the orchestration (fan-out, ordering, aggregation, isolation), not the
sub-agent's reasoning."""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from ronin_cli import code_mode
from ronin_cli.code_mode import (
    CodeRunResult,
    build_isolated_task_tool,
    build_parallel_task_tool,
)
from ronin_cli.config import RoninConfig


def _cfg() -> RoninConfig:
    return RoninConfig(provider="anthropic", model="claude-sonnet-4-6")


def _init_repo(path: Path) -> None:
    def git(*args):
        subprocess.run(["git", "-C", str(path), *args], check=True,
                       capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "t@t.dev")
    git("config", "user.name", "Test")
    git("config", "commit.gpgsign", "false")
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")


# ---------- parallel_task (read-only fan-out) ----------


def test_parallel_task_runs_all_and_aggregates(monkeypatch, tmp_path) -> None:
    calls: list[str] = []

    def fake_run(config, prompt, **kwargs):
        calls.append(prompt)
        assert kwargs["read_only"] is True  # read-only fan-out
        return CodeRunResult(success=True, output=f"answer to: {prompt}", iterations=1)

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    tool = build_parallel_task_tool(_cfg(), tmp_path)
    out = tool.handler(tasks=[
        {"description": "A", "prompt": "how does auth work"},
        {"description": "B", "prompt": "where is rate limiting"},
    ])

    assert set(calls) == {"how does auth work", "where is rate limiting"}
    # aggregated under each label, input order preserved
    assert out.index("### A") < out.index("### B")
    assert "answer to: how does auth work" in out


def test_parallel_task_actually_runs_concurrently(monkeypatch, tmp_path) -> None:
    barrier = threading.Barrier(3, timeout=5)

    def fake_run(config, prompt, **kwargs):
        # if these don't run concurrently, the barrier times out → test fails
        barrier.wait()
        return CodeRunResult(success=True, output="ok", iterations=1)

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    tool = build_parallel_task_tool(_cfg(), tmp_path, max_workers=3)
    out = tool.handler(tasks=[{"description": f"t{i}", "prompt": str(i)} for i in range(3)])
    assert out.count("### t") == 3


def test_parallel_task_empty() -> None:
    tool = build_parallel_task_tool(_cfg(), ".")
    assert "no tasks" in tool.handler(tasks=[])


def test_parallel_task_reports_subagent_error(monkeypatch, tmp_path) -> None:
    def fake_run(config, prompt, **kwargs):
        return CodeRunResult(success=False, output="", iterations=1, error="boom")

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    tool = build_parallel_task_tool(_cfg(), tmp_path)
    out = tool.handler(tasks=[{"description": "X", "prompt": "p"}])
    assert "sub-agent error: boom" in out


# ---------- isolated_task (mutating, worktree-isolated) ----------


def test_isolated_task_requires_git_repo(tmp_path) -> None:
    tool = build_isolated_task_tool(_cfg(), tmp_path)  # not a git repo
    assert "needs a git repository" in tool.handler(tasks=[{"description": "x", "prompt": "p"}])


def test_isolated_task_runs_in_worktree_and_returns_diff(monkeypatch, tmp_path) -> None:
    _init_repo(tmp_path)
    seen_roots: list[Path] = []

    def fake_run(config, prompt, *, root, read_only, **kwargs):
        # the sub-agent must run in an isolated worktree, NOT the main checkout
        assert read_only is False           # isolated agents may mutate
        assert Path(root) != tmp_path
        seen_roots.append(Path(root))
        # simulate the agent creating a file in its worktree
        (Path(root) / "feature.py").write_text("def feature():\n    pass\n", encoding="utf-8")
        return CodeRunResult(success=True, output="added feature", iterations=2)

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    tool = build_isolated_task_tool(_cfg(), tmp_path)
    out = tool.handler(tasks=[{"description": "feat", "prompt": "add a feature"}])

    assert "### feat" in out
    assert "added feature" in out
    assert "feature.py" in out          # the diff captured the new file
    assert "def feature" in out
    # the main checkout was never touched
    assert not (tmp_path / "feature.py").exists()
    # and the worktree was cleaned up
    assert seen_roots and not seen_roots[0].exists()


def test_isolated_task_parallel_no_collision(monkeypatch, tmp_path) -> None:
    _init_repo(tmp_path)

    def fake_run(config, prompt, *, root, **kwargs):
        # each agent writes a file named after its prompt into its own worktree
        (Path(root) / f"{prompt}.py").write_text(f"# {prompt}\n", encoding="utf-8")
        time.sleep(0.01)
        return CodeRunResult(success=True, output=f"did {prompt}", iterations=1)

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    tool = build_isolated_task_tool(_cfg(), tmp_path, max_workers=3)
    out = tool.handler(tasks=[
        {"description": "one", "prompt": "alpha"},
        {"description": "two", "prompt": "beta"},
    ])
    # each isolated diff only contains its own file — no cross-contamination
    alpha_section = out.split("### two")[0]
    assert "alpha.py" in alpha_section and "beta.py" not in alpha_section
