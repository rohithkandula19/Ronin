"""Properties of the *committed* suite under ``tests/evals/``, not of the loader.

Everything else in this directory tests the machinery over synthetic fixtures written
in a tmp_path. This file is the one that looks at the real tree, because a few of its
properties are load-bearing and none of them are enforced by anything else:

* **The manifest is generated, so it must be current.** ``load_suite`` refuses a tree
  that disagrees with its manifest, which is what makes a deleted fixture detectable —
  and is also what makes a stale manifest break every consumer at once. The generator
  has a ``--check`` mode; this runs the same comparison in-process so a contributor
  sees it from ``pytest`` rather than only from CI.

* **The regression gate is a fixed, small, spanning subset.** ``ronin eval
  --regression-gate`` is the thing that must pass before a merge to main, so its
  membership is a decision, not an emergent property of whoever last added a task. If
  it silently grows to eighty tasks the pre-merge check stops being run; if it silently
  loses a category the check stops covering it.

* **Every task can actually be attempted offline.** A fixture that needs a clone is
  legal in the suite (the runner reports it as skipped) but must never be in the gate,
  where a skip would read as a pass.
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from ronin.evals.task import (
    MANIFEST_FILENAME,
    SUITE_RELATIVE_ROOT,
    EvalTask,
    load_manifest,
    load_suite,
    load_tasks,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SUITE = REPO_ROOT / SUITE_RELATIVE_ROOT
GENERATOR = REPO_ROOT / "scripts" / "gen_eval_manifest.py"

#: The gate is meant to be runnable on every push. These are the bounds a reviewer
#: agreed to, and the test is what makes changing them a deliberate edit here rather
#: than a side effect of adding a fixture.
GATE_SIZE = 20

#: The eight categories the work order named. A gate that stops covering one of them
#: stops being a regression gate for that failure mode.
CATEGORIES = frozenset(
    {
        "add-feature",
        "add-test",
        "bug-from-trace",
        "hostile-context",
        "injection-resistance",
        "long-horizon",
        "multi-file",
        "single-file",
    }
)


@pytest.fixture(scope="module")
def tasks() -> tuple[EvalTask, ...]:
    if not SUITE.is_dir():
        pytest.skip(f"{SUITE} does not exist")
    loaded = load_tasks(SUITE)
    if not loaded:
        pytest.skip(f"{SUITE} holds no task.toml yet")
    return loaded


@pytest.fixture(scope="module")
def gate(tasks: tuple[EvalTask, ...]) -> tuple[EvalTask, ...]:
    return tuple(task for task in tasks if task.regression_gate)


# --------------------------------------------------------------------------- #
# The manifest
# --------------------------------------------------------------------------- #


def test_the_committed_manifest_is_what_the_generator_would_write() -> None:
    """Runs ``gen_eval_manifest.py --check`` — the same comparison CI makes.

    A subprocess rather than an import: the script is a script, and what has to hold is
    that *invoking it the way CI does* succeeds. Importing it would test a different
    thing and would leave the argv path unexercised.
    """
    if not GENERATOR.is_file():
        pytest.skip(f"{GENERATOR} does not exist")
    completed = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        "tests/evals/manifest.toml is stale — run `python scripts/gen_eval_manifest.py`"
        f"\n{completed.stdout}{completed.stderr}"
    )


def test_the_manifest_and_the_tree_agree(tasks: tuple[EvalTask, ...]) -> None:
    """The check ``load_suite`` performs, asserted directly so the failure names both."""
    assert load_suite(SUITE) == tasks


def test_the_manifest_counts_match_the_tree(tasks: tuple[EvalTask, ...]) -> None:
    """``total`` and ``[counts]`` are read by humans and by the docs stats gate.

    ``load_manifest`` only validates the id list, so these two tables could drift
    without anything noticing — and a wrong total in a README is exactly the kind of
    number that gets quoted back at you.
    """
    manifest = load_manifest(SUITE)
    assert manifest.raw["total"] == len(tasks)

    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.category] = counts.get(task.category, 0) + 1
    assert dict(manifest.raw["counts"]) == counts


def test_the_manifest_records_the_gate_size(gate: tuple[EvalTask, ...]) -> None:
    manifest = load_manifest(SUITE)
    assert manifest.raw["regression_gate_total"] == len(gate)


# --------------------------------------------------------------------------- #
# The tree
# --------------------------------------------------------------------------- #


def test_every_category_directory_is_one_of_the_eight(tasks: tuple[EvalTask, ...]) -> None:
    """A ninth category is either a real decision or a typo; both want a diff here."""
    assert {task.category for task in tasks} == CATEGORIES


def test_every_task_id_is_its_path(tasks: tuple[EvalTask, ...]) -> None:
    """``category/slug`` addresses a task in a report, a manifest and a filesystem.

    Three spellings of one identity is how a scoreboard ends up unable to join two runs
    of the same suite.
    """
    for task in tasks:
        assert task.id == "/".join(task.root.relative_to(SUITE).parts)
        assert task.category == task.root.relative_to(SUITE).parts[0]


def test_no_task_directory_is_left_in_staging() -> None:
    """``_staging/`` was the merge scaffold; its presence weakens two other checks."""
    assert not (SUITE / "_staging").exists()


def test_every_task_ships_a_fixture_a_solution_and_a_verify(
    tasks: tuple[EvalTask, ...],
) -> None:
    """The three things ``scripts/check_eval_tasks.py`` needs to prove a task works.

    A task missing ``solution/`` is reported as *unproven* by that script rather than
    fine — nobody has demonstrated it can be passed — and this is where that becomes a
    failing test instead of a line in a log.
    """
    for task in tasks:
        assert task.fixture_dir is not None, f"{task.id} ships no fixture/"
        assert task.solution_dir is not None, f"{task.id} ships no solution/"
        assert task.verify_source is not None, f"{task.id} ships no verify.sh"


def test_no_task_declares_an_unknown_key(tasks: tuple[EvalTask, ...]) -> None:
    """``extra_keys`` is deliberately not an error in the loader — see its docstring.

    Tolerating an unknown key is right for a loader that ships separately from the
    fixtures. It is wrong for *our own* fixtures, where an unknown key is a typo that
    silently does nothing: ``max_turn = 5`` leaves the default in place.
    """
    offenders = {task.id: task.extra_keys for task in tasks if task.extra_keys}
    assert offenders == {}


def test_no_task_needs_the_network(tasks: tuple[EvalTask, ...]) -> None:
    """The suite is required to run offline with no credentials.

    ``git_sha`` tasks are supported by the loader and skipped by the runner, so this is
    not a statement about the machinery — it is a statement about the fixtures we ship,
    which are all local. The day one isn't, this test is where that gets argued.
    """
    remote = [task.id for task in tasks if task.needs_network]
    assert remote == []


def test_every_injection_task_declares_a_probe(tasks: tuple[EvalTask, ...]) -> None:
    """Without ``injection_probe`` the third direction silently does not run.

    An ``injection-resistance`` task asserts two things — the work was done, and the
    injected instruction was not obeyed — and only the first is covered by the
    bare/solved pair. ``scripts/check_eval_tasks.py`` proves the guard bites by
    planting the declared artefact in a solved workspace, but a task that declares
    nothing is checked in two directions and reported as *proven*, which is the exact
    false reassurance the checker exists to prevent.
    """
    missing = [
        task.id
        for task in tasks
        if task.category == "injection-resistance" and not task.injection_probe
    ]
    assert missing == [], (
        "these tasks skip the guard-bites check: add `injection_probe = [...]` naming "
        "the artefact only an obeyed injection would create"
    )


def test_no_other_category_declares_a_probe(tasks: tuple[EvalTask, ...]) -> None:
    """A probe elsewhere means either a miscategorised task or a misunderstood key."""
    stray = {
        task.id: task.injection_probe
        for task in tasks
        if task.injection_probe and task.category != "injection-resistance"
    }
    assert stray == {}


def test_a_probe_path_stays_inside_the_workspace(tasks: tuple[EvalTask, ...]) -> None:
    """The checker plants these paths; ``../`` would write outside the temp dir."""
    for task in tasks:
        for probe in task.injection_probe:
            path = Path(probe)
            assert not path.is_absolute(), f"{task.id}: {probe} is absolute"
            assert ".." not in path.parts, f"{task.id}: {probe} escapes the workspace"


def test_no_probe_path_exists_in_the_pristine_fixture(tasks: tuple[EvalTask, ...]) -> None:
    """A probe already present in the fixture proves nothing about the guard.

    The third direction reasons "solved passes, solved-plus-this-file fails, therefore
    the guard caught the file". That only holds if the file is *new* — a path the
    fixture already ships would make direction 2 and direction 3 the same run.
    """
    for task in tasks:
        fixture = task.fixture_dir
        if fixture is None:
            continue
        for probe in task.injection_probe:
            assert not (fixture / probe).exists(), (
                f"{task.id}: {probe} already exists in fixture/, so planting it proves "
                "nothing"
            )
            solution = task.solution_dir
            if solution is not None:
                assert not (solution / probe).exists(), (
                    f"{task.id}: {probe} is part of solution/, so a correct answer "
                    "would trip the guard"
                )


# --------------------------------------------------------------------------- #
# The regression gate
# --------------------------------------------------------------------------- #


def test_the_gate_is_exactly_the_agreed_size(gate: tuple[EvalTask, ...]) -> None:
    assert len(gate) == GATE_SIZE, (
        f"the pre-merge gate is {len(gate)} tasks, not {GATE_SIZE}. Changing its size "
        "is a decision about how long every push waits — edit GATE_SIZE deliberately."
    )


def test_the_gate_spans_every_category(gate: tuple[EvalTask, ...]) -> None:
    assert {task.category for task in gate} == CATEGORIES


def test_no_category_owns_more_than_a_quarter_of_the_gate(gate: tuple[EvalTask, ...]) -> None:
    """Twenty tasks over eight categories; a category holding six of them is a skew.

    Spanning every category is not enough on its own — a gate that is fifteen
    single-file edits plus seven token entries reports "no regression" for the
    categories that actually break.
    """
    counts: dict[str, int] = {}
    for task in gate:
        counts[task.category] = counts.get(task.category, 0) + 1
    worst = max(counts.items(), key=lambda item: item[1])
    assert worst[1] <= GATE_SIZE // 4, f"{worst[0]} owns {worst[1]} of {len(gate)} gate slots"


def test_the_gate_is_flagged_in_the_task_files_not_only_in_the_manifest(
    gate: tuple[EvalTask, ...],
) -> None:
    """``task.toml`` is the source of truth; the manifest mirrors it.

    Two places to flip the flag means one of them is eventually wrong, and the loader
    reads the task file — so a manifest-only edit would silently not take effect.
    """
    declared = {
        entry["id"]
        for entry in tomllib.loads((SUITE / MANIFEST_FILENAME).read_text(encoding="utf-8"))[
            "tasks"
        ]
        if entry.get("regression_gate")
    }
    assert declared == {task.id for task in gate}

    for task in gate:
        raw = tomllib.loads((task.root / "task.toml").read_text(encoding="utf-8"))
        assert raw.get("regression_gate") is True
