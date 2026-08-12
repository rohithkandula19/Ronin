"""``ronin.evals.poolgen`` — the generated training pool, proven offline.

The pool's whole worth is that a generated ``verify.sh`` *discriminates*: it fails on the
bare fixture and passes on the reference solution. So the load-bearing test here does not
trust the generator's word — it runs each generated ``verify.sh`` both ways under ``bash``
in a temp workspace, which is the same two-way check ``scripts/check_eval_tasks.py`` runs
in CI and the same script a container would run. Everything else (determinism, the
eval-suite write guard, the PR-miner's drop rule, the container argv) is a pure-function
assertion with no subprocess.
"""

from __future__ import annotations

import random
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from ronin.evals.poolgen import (
    DEFAULT_IMAGE,
    EVAL_SUITE_MARKER,
    GeneratedTask,
    MinedPR,
    bug_injection_tasks,
    container_command,
    eval_style_tasks,
    generate_pool,
    mine_prs,
    write_pool,
)

# --------------------------------------------------------------------------- #
# The discrimination proof — run verify.sh both ways, offline, under bash
# --------------------------------------------------------------------------- #


def _run_verify(task_dir: Path, *, overlay_solution: bool) -> int:
    """Copy ``fixture/`` to a workspace, optionally overlay ``solution/``, run verify.sh.

    Returns the exit code. This mirrors ``scripts/check_eval_tasks.py`` exactly: bare
    fixture must exit non-zero, solution-overlaid must exit zero.
    """
    workspace = task_dir / "_ws"
    if workspace.exists():
        shutil.rmtree(workspace)
    shutil.copytree(task_dir / "fixture", workspace)
    if overlay_solution:
        for path in sorted((task_dir / "solution").rglob("*")):
            if path.is_file():
                rel = path.relative_to(task_dir / "solution")
                target = workspace / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    proc = subprocess.run(
        ["bash", str(task_dir / "verify.sh")],
        cwd=workspace,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "RONIN_EVAL_PYTHON": sys.executable},
    )
    return proc.returncode


@pytest.mark.parametrize("count", [20])
def test_every_generated_task_discriminates_bare_fails_solution_passes(
    tmp_path: Path, count: int
) -> None:
    """The acceptance: N generated tasks, each fails on the no-op and passes on solution."""
    pool = write_pool(generate_pool(count, seed=0), tmp_path / "pool")
    task_dirs = sorted(p.parent for p in pool.rglob("verify.sh"))
    assert len(task_dirs) == count, "one verify.sh per task"
    for task_dir in task_dirs:
        assert _run_verify(task_dir, overlay_solution=False) != 0, f"{task_dir.name}: bare passed"
        assert _run_verify(task_dir, overlay_solution=True) == 0, (
            f"{task_dir.name}: solution failed"
        )


# --------------------------------------------------------------------------- #
# Determinism, capacity, dedup
# --------------------------------------------------------------------------- #


def test_the_same_seed_produces_a_byte_identical_pool() -> None:
    a = generate_pool(50, seed=7)
    b = generate_pool(50, seed=7)
    assert [t.task_id for t in a] == [t.task_id for t in b]
    assert [t.content_hash() for t in a] == [t.content_hash() for t in b]


def test_a_different_seed_produces_a_different_pool() -> None:
    a = generate_pool(50, seed=1)
    b = generate_pool(50, seed=2)
    assert [t.content_hash() for t in a] != [t.content_hash() for t in b]


@pytest.mark.parametrize("count", [20, 800, 1500])
def test_the_pool_reaches_the_requested_size_with_unique_ids(count: int) -> None:
    tasks = generate_pool(count, seed=0)
    assert len(tasks) == count
    assert len({t.task_id for t in tasks}) == count, "ids are unique (no on-disk overwrite)"
    assert len({t.content_hash() for t in tasks}) == count, "content is unique (real dedup)"


def test_both_offline_sources_are_represented() -> None:
    sources = {t.source for t in generate_pool(20, seed=0)}
    assert "bug-injection" in sources and "eval-style" in sources


def test_a_zero_count_is_refused() -> None:
    with pytest.raises(ValueError, match="count must be"):
        generate_pool(0)


# --------------------------------------------------------------------------- #
# The eval-suite write guard — the pool must never become training-on-the-benchmark
# --------------------------------------------------------------------------- #


def test_writing_into_the_eval_suite_is_refused(tmp_path: Path) -> None:
    suite = tmp_path.joinpath(*EVAL_SUITE_MARKER, "_staging")
    with pytest.raises(ValueError, match="refusing to write a task pool into the eval suite"):
        write_pool(generate_pool(3, seed=0), suite)


def test_the_guard_can_be_overridden_only_explicitly(tmp_path: Path) -> None:
    # The escape hatch exists solely so this test can prove the path detection is right;
    # nothing in the product passes allow_eval_suite=True.
    suite = tmp_path.joinpath(*EVAL_SUITE_MARKER, "_staging")
    out = write_pool(generate_pool(3, seed=0), suite, allow_eval_suite=True)
    assert out.exists()


def test_a_normal_pool_directory_is_allowed(tmp_path: Path) -> None:
    out = write_pool(generate_pool(3, seed=0), tmp_path / "pool")
    assert (out / "manifest.toml").exists()


# --------------------------------------------------------------------------- #
# On-disk shape — a generated task is what a suite loader reads
# --------------------------------------------------------------------------- #


def test_a_written_task_has_the_eval_suite_shape(tmp_path: Path) -> None:
    task = generate_pool(20, seed=0)[0]
    directory = task.write(tmp_path / "one")
    assert (directory / "task.toml").is_file()
    assert (directory / "fixture").is_dir()
    assert (directory / "solution").is_dir()
    verify = directory / "verify.sh"
    assert verify.is_file()
    parsed = tomllib.loads((directory / "task.toml").read_text(encoding="utf-8"))
    for key in ("id", "category", "prompt", "fixture", "files_expected", "max_turns", "source"):
        assert key in parsed, f"task.toml missing {key}"
    assert parsed["regression_gate"] is False, "a pool task is never a gate task"


def test_a_task_without_a_solution_is_refused() -> None:
    with pytest.raises(ValueError, match="reference solution"):
        GeneratedTask(
            task_id="single-file/x",
            category="single-file",
            prompt="p",
            fixture={"a.py": "x"},
            solution={},
            verify="#!/usr/bin/env bash\nexit 0\n",
            source="eval-style",
        )


# --------------------------------------------------------------------------- #
# PR mining — offline, behind the fetcher seam
# --------------------------------------------------------------------------- #


def _pr(number: int, *, tests: dict[str, str], fixes: dict[str, str]) -> MinedPR:
    return MinedPR(
        repo="acme/widget",
        number=number,
        issue_title=f"Bug {number}",
        issue_body="reproduce by running the suite",
        test_files=tests,
        fix_files=fixes,
    )


def test_pr_mining_keeps_only_prs_with_both_a_test_and_a_fix() -> None:
    prs = [
        _pr(1, tests={"test_a.py": "def test_a(): assert True\n"}, fixes={"a.py": "x = 1\n"}),
        _pr(2, tests={}, fixes={"b.py": "y = 2\n"}),  # no test → cannot verify
        _pr(3, tests={"test_c.py": "..."}, fixes={}),  # no fix → no solution
    ]
    tasks = mine_prs(lambda repo, limit: prs, repo="acme/widget", limit=10)
    assert [t.task_id for t in tasks] == ["pr-mined/acme-widget-1"]
    only = tasks[0]
    assert "test_a.py" in only.fixture and "a.py" in only.solution
    assert only.prompt.startswith("Bug 1")
    assert "reproduce by running the suite" in only.prompt
    assert only.source == "pr-mined"


def test_pr_mining_over_an_empty_fetch_yields_nothing() -> None:
    assert mine_prs(lambda repo, limit: [], repo="acme/widget", limit=10) == []


# --------------------------------------------------------------------------- #
# The reproducible-container command — pure argv, no daemon needed
# --------------------------------------------------------------------------- #


def test_container_command_isolates_the_network_and_mounts_the_task(tmp_path: Path) -> None:
    argv = container_command(tmp_path / "task", image="python:3.11-slim")
    assert argv[0:3] == ["docker", "run", "--rm"]
    assert "--network=none" in argv, "a reproducible verify must not reach the network"
    assert "python:3.11-slim" in argv
    assert argv[-3:] == ["python:3.11-slim", "bash", "verify.sh"]
    mount = argv[argv.index("-v") + 1]
    assert mount.endswith(":/task:rw") and str((tmp_path / "task").resolve()) in mount


def test_container_command_can_opt_into_the_network() -> None:
    argv = container_command(Path("/t"), network=True)
    assert "--network=bridge" in argv


def test_the_default_image_is_stdlib_python() -> None:
    assert "python" in DEFAULT_IMAGE


# --------------------------------------------------------------------------- #
# The generators, called directly
# --------------------------------------------------------------------------- #


def test_bug_injection_capacity_is_the_seed_mutation_product() -> None:
    rng = random.Random(0)
    everything = bug_injection_tasks(rng, 10_000)
    assert len(everything) >= 6, "at least the curated seed-by-mutation product"
    assert all(t.source == "bug-injection" for t in everything)


def test_eval_style_cycles_the_families() -> None:
    rng = random.Random(0)
    tasks = eval_style_tasks(rng, 6)
    assert len(tasks) == 6
    assert all(t.source == "eval-style" for t in tasks)


@pytest.mark.skipif(
    shutil.which("docker") is None
    or subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="no working docker daemon; the container path is argv-tested instead",
)
def test_verify_runs_inside_a_real_container(tmp_path: Path) -> None:  # pragma: no cover
    """When a daemon exists, the same verify.sh that passes under bash passes in a container.

    Auto-skipped here (no daemon), matching the repo's existing skipif-on-missing-tool
    pattern; it runs the reproducible path for real wherever a daemon is present.
    """
    task = generate_pool(1, seed=0)[0]
    directory = task.write(tmp_path / "pool")
    workspace = tmp_path / "ws"
    shutil.copytree(directory / "fixture", workspace)
    for path in sorted((directory / "solution").rglob("*")):
        if path.is_file():
            shutil.copy2(path, workspace / path.relative_to(directory / "solution"))
    (workspace / "verify.sh").write_text((directory / "verify.sh").read_text())
    proc = subprocess.run(container_command(workspace), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
