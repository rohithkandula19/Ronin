"""``ronin.evals.swebench`` — the external signal, its honesty, and its refusals.

Nothing here touches the network, and nothing here is a real SWE-bench-lite row. The
three records in :data:`FIXTURE_JSONL` are **hand-written fixtures in the published
schema**, written to prove the parser and the resolution rule; they are not dataset
instances and no number derived from them says anything about any model.

The one subprocess used is ``git`` against a repository created inside ``tmp_path``,
which is genuinely local and costs nothing.
"""
from __future__ import annotations

import inspect
import json
import subprocess
from dataclasses import dataclass, fields
from pathlib import Path

import pytest

from ronin.evals.swebench import (
    REPORT_SUFFIXES,
    SWEBENCH_DISCLAIMER,
    AgentLike,
    AgentRunLike,
    DatasetUnavailable,
    PatchAttempt,
    SWEBenchDataset,
    SWEBenchHarness,
    SWEBenchOutcome,
    SWEBenchRun,
    SWEBenchTask,
    TaskEvaluation,
    TrainingSinkRefused,
    agent_patch_runner,
    default_prompt,
    did_not_run,
    git_diff_capture,
    is_resolved,
    load_dataset,
    oracle_patch_runner,
    read_jsonl,
    render_markdown,
    run_or_report,
    write_report,
)

# --------------------------------------------------------------------------- #
# The fixture — HAND-WRITTEN, in the real schema. Not dataset rows.
# --------------------------------------------------------------------------- #

#: Three fabricated instances in SWE-bench's published shape. Row 1 stores its test
#: lists as JSON *strings*, exactly as the published JSONL does; row 2 stores them as
#: arrays, which a HuggingFace export produces; row 3 has no gold patch. The mix is
#: the point — a loader that handles only one form scores a real dataset as zero.
FIXTURE_JSONL = "\n".join(
    [
        "# HAND-WRITTEN FIXTURES in the SWE-bench schema. Not real dataset rows.",
        json.dumps(
            {
                "instance_id": "ronin__fixture-1",
                "repo": "ronin/fixture",
                "base_commit": "0" * 40,
                "problem_statement": "add() returns the wrong sign for negatives.",
                "test_patch": "--- a/tests/test_a.py\n+++ b/tests/test_a.py\n",
                "patch": "--- a/fixture/add.py\n+++ b/fixture/add.py\n@@\n-    return a - b\n+    return a + b\n",
                "FAIL_TO_PASS": '["tests/test_a.py::test_negatives"]',
                "PASS_TO_PASS": '["tests/test_a.py::test_positives"]',
                "hints_text": "",
                "version": "1.0",
            }
        ),
        json.dumps(
            {
                "instance_id": "ronin__fixture-2",
                "repo": "ronin/fixture",
                "base_commit": "1" * 40,
                "problem_statement": "parse() drops the last field.",
                "test_patch": "",
                "patch": "--- a/fixture/parse.py\n+++ b/fixture/parse.py\n",
                "FAIL_TO_PASS": ["tests/test_b.py::test_last_field"],
                "PASS_TO_PASS": ["tests/test_b.py::test_first_field"],
                "version": "1.1",
            }
        ),
        json.dumps(
            {
                "instance_id": "other__fixture-3",
                "repo": "other/fixture",
                "base_commit": "2" * 40,
                "problem_statement": "no gold patch is shipped for this one.",
                "test_patch": "",
                "patch": None,
                "FAIL_TO_PASS": '["tests/test_c.py::test_thing"]',
                "PASS_TO_PASS": "[]",
            }
        ),
        "",
    ]
)


@pytest.fixture()
def fixture_path(tmp_path: Path) -> Path:
    path = tmp_path / "swebench_fixture.jsonl"
    path.write_text(FIXTURE_JSONL, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Parsing the published schema
# --------------------------------------------------------------------------- #


def test_the_committed_fixture_parses_in_the_published_schema(fixture_path: Path) -> None:
    dataset = load_dataset(path=fixture_path)
    assert len(dataset) == 3
    first = dataset.tasks[0]
    assert first.instance_id == "ronin__fixture-1"
    assert first.fail_to_pass == ("tests/test_a.py::test_negatives",)
    assert first.pass_to_pass == ("tests/test_a.py::test_positives",)
    assert first.required_tests == (
        "tests/test_a.py::test_negatives",
        "tests/test_a.py::test_positives",
    )


def test_test_lists_load_from_a_json_string_and_from_an_array(fixture_path: Path) -> None:
    """The published JSONL stores them as strings; an export stores them as arrays."""
    dataset = load_dataset(path=fixture_path)
    assert dataset.tasks[0].fail_to_pass == ("tests/test_a.py::test_negatives",)
    assert dataset.tasks[1].fail_to_pass == ("tests/test_b.py::test_last_field",)
    assert dataset.tasks[2].pass_to_pass == ()


def test_a_null_gold_patch_stays_none_rather_than_becoming_the_string_none(
    fixture_path: Path,
) -> None:
    assert load_dataset(path=fixture_path).tasks[2].patch is None


def test_comment_and_blank_lines_are_skipped(fixture_path: Path) -> None:
    assert len(read_jsonl(fixture_path)) == 3


def test_a_row_with_a_non_list_test_field_is_rejected_by_name(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({"instance_id": "x-1", "FAIL_TO_PASS": 7}) + "\n", "utf-8")
    with pytest.raises(DatasetUnavailable, match="not in the SWE-bench schema"):
        load_dataset(path=path)


def test_a_duplicate_instance_id_is_refused(tmp_path: Path) -> None:
    row = json.dumps({"instance_id": "x-1", "FAIL_TO_PASS": ["t"]})
    path = tmp_path / "dupe.jsonl"
    path.write_text(f"{row}\n{row}\n", "utf-8")
    with pytest.raises(DatasetUnavailable, match="duplicate instance_id"):
        load_dataset(path=path)


def test_an_injected_fetcher_stands_in_for_the_network(fixture_path: Path) -> None:
    """The published dataset needs network access, so the loader takes a fetcher and
    never reaches for one itself."""
    rows = read_jsonl(fixture_path)
    dataset = load_dataset(fetch=lambda: rows)
    assert [task.instance_id for task in dataset] == [
        "ronin__fixture-1",
        "ronin__fixture-2",
        "other__fixture-3",
    ]


def test_subset_narrows_by_ids_then_repo_then_limit(fixture_path: Path) -> None:
    dataset = load_dataset(path=fixture_path)
    assert dataset.repos() == ("other/fixture", "ronin/fixture")
    assert len(dataset.subset(repo="ronin/fixture")) == 2
    assert len(dataset.subset(limit=1)) == 1
    assert [t.instance_id for t in dataset.subset(ids=["other__fixture-3"])] == [
        "other__fixture-3"
    ]


# --------------------------------------------------------------------------- #
# The resolution rule
# --------------------------------------------------------------------------- #

_TASK = SWEBenchTask(
    instance_id="x-1",
    fail_to_pass=("f1", "f2"),
    pass_to_pass=("p1",),
)


@pytest.mark.parametrize(
    ("evaluation", "expected", "why"),
    [
        (TaskEvaluation(passed_tests=("f1", "f2", "p1")), True, "everything required passed"),
        (TaskEvaluation(passed_tests=("f1", "p1"), failed_tests=("f2",)), False, "partial"),
        (
            TaskEvaluation(passed_tests=("f1", "f2"), failed_tests=("p1",)),
            False,
            "regression in PASS_TO_PASS",
        ),
        (TaskEvaluation(passed_tests=("f1", "f2", "p1", "extra")), True, "extras are ignored"),
        (
            TaskEvaluation(passed_tests=("f1", "f2", "p1"), error="container died"),
            False,
            "a harness error is never resolved",
        ),
        (TaskEvaluation(), False, "nothing ran"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_the_resolution_rule_matches_swebench(
    evaluation: TaskEvaluation, expected: bool, why: str
) -> None:
    assert is_resolved(_TASK, evaluation) is expected, why


def test_a_task_with_no_required_tests_is_never_resolved() -> None:
    """"All zero required tests passed" is vacuously true, and would score an
    unparseable row as a success."""
    empty = SWEBenchTask(instance_id="x-2")
    assert is_resolved(empty, TaskEvaluation(passed_tests=("anything",))) is False


def test_a_partial_pass_is_visible_as_partial_not_just_as_unresolved() -> None:
    outcome = SWEBenchOutcome(
        instance_id="x-1",
        patch_generated=True,
        fail_to_pass_passed=1,
        fail_to_pass_total=2,
        pass_to_pass_passed=1,
        pass_to_pass_total=1,
    )
    assert outcome.partial
    assert outcome.status == "unresolved"


def test_a_patch_that_did_nothing_is_not_partial() -> None:
    outcome = SWEBenchOutcome(
        instance_id="x-1", patch_generated=True, fail_to_pass_total=2, pass_to_pass_total=1
    )
    assert not outcome.partial


def test_the_v2_rule_agrees_with_the_eval_suite_rule_on_every_case() -> None:
    """A drift gate against ``packages/eval-suite``.

    ``ronin.evals`` is dependency-free by design and cannot import the pydantic-based
    v1 model, so the rule exists in two places. That duplication is only safe if
    something checks it, which is this test — skipped, not silently passed, when the
    v1 package is not installed.
    """
    v1 = pytest.importorskip("ronin_eval_suite.swebench")
    v1_task = v1.SWEBenchTask(instance_id="x-1", FAIL_TO_PASS=["f1", "f2"], PASS_TO_PASS=["p1"])
    cases = [
        (("f1", "f2", "p1"), ""),
        (("f1", "p1"), ""),
        (("f1", "f2"), ""),
        ((), ""),
        (("f1", "f2", "p1"), "boom"),
    ]
    for passed, error in cases:
        mine = is_resolved(_TASK, TaskEvaluation(passed_tests=passed, error=error))
        theirs = v1.is_resolved(
            v1_task,
            v1.TaskEvaluation(passed_tests=list(passed), error=error or None),
        )
        assert mine is theirs, f"rules disagree on {passed!r} / {error!r}"


def test_the_committed_fixture_also_validates_under_the_eval_suite_model(
    fixture_path: Path,
) -> None:
    """If a fixture only parses here, it proves nothing about the published schema."""
    v1 = pytest.importorskip("ronin_eval_suite.swebench")
    mine = load_dataset(path=fixture_path)
    for row, task in zip(read_jsonl(fixture_path), mine.tasks, strict=True):
        theirs = v1.SWEBenchTask.model_validate(dict(row))
        assert theirs.instance_id == task.instance_id
        assert tuple(theirs.fail_to_pass) == task.fail_to_pass
        assert tuple(theirs.pass_to_pass) == task.pass_to_pass


# --------------------------------------------------------------------------- #
# "Did not run" is a result, never a zero
# --------------------------------------------------------------------------- #


def test_a_run_with_no_outcomes_must_say_why() -> None:
    with pytest.raises(ValueError, match="must say why it did not run"):
        SWEBenchRun()


def test_a_run_cannot_both_have_outcomes_and_claim_it_did_not_run() -> None:
    with pytest.raises(ValueError, match="cannot both"):
        SWEBenchRun(outcomes=(SWEBenchOutcome(instance_id="x-1"),), did_not_run="nope")


def test_did_not_run_needs_an_actionable_reason() -> None:
    with pytest.raises(ValueError, match="reason a human can act on"):
        did_not_run("   ")


def test_a_run_that_did_not_happen_has_no_resolve_rate() -> None:
    run = did_not_run("dataset absent")
    assert run.ran is False
    assert run.resolved_rate is None


async def test_a_missing_dataset_reports_that_it_did_not_run_rather_than_a_zero_score(
    tmp_path: Path,
) -> None:
    harness = SWEBenchHarness(
        patch_runner=oracle_patch_runner(),
        evaluator=_always_passes,
        workspace=lambda task: tmp_path,
        label="ronin v2",
    )
    run = await run_or_report(harness, path=tmp_path / "absent.jsonl")
    assert run.ran is False
    assert "absent.jsonl" in run.did_not_run
    assert run.resolved_rate is None

    rendered = render_markdown(run)
    assert "did not run" in rendered
    assert "%" not in rendered
    assert "0/0" not in rendered
    assert SWEBENCH_DISCLAIMER in rendered
    assert "network access" in rendered


async def test_no_dataset_source_at_all_is_a_did_not_run_with_instructions(
    tmp_path: Path,
) -> None:
    harness = SWEBenchHarness(
        patch_runner=oracle_patch_runner(),
        evaluator=_always_passes,
        workspace=lambda task: tmp_path,
    )
    run = await run_or_report(harness)
    assert run.ran is False
    assert "does not download it" in run.did_not_run


async def test_an_empty_dataset_is_a_did_not_run(tmp_path: Path) -> None:
    harness = SWEBenchHarness(
        patch_runner=oracle_patch_runner(),
        evaluator=_always_passes,
        workspace=lambda task: tmp_path,
    )
    run = await harness.run(SWEBenchDataset())
    assert run.ran is False
    assert "empty" in run.did_not_run


# --------------------------------------------------------------------------- #
# Running and scoring
# --------------------------------------------------------------------------- #


async def _always_passes(task: SWEBenchTask, patch: str, workdir: Path) -> TaskEvaluation:
    return TaskEvaluation(passed_tests=task.required_tests)


async def _first_test_only(task: SWEBenchTask, patch: str, workdir: Path) -> TaskEvaluation:
    return TaskEvaluation(
        passed_tests=task.required_tests[:1], failed_tests=task.required_tests[1:]
    )


async def test_the_oracle_control_resolves_every_task_with_a_gold_patch(
    fixture_path: Path, tmp_path: Path
) -> None:
    """A gold patch that fails indicts the evaluator, never the agent — which is what
    makes this the first thing to run when a rate looks wrong."""
    harness = SWEBenchHarness(
        patch_runner=oracle_patch_runner(),
        evaluator=_always_passes,
        workspace=lambda task: tmp_path,
        label="oracle control",
    )
    run = await run_or_report(harness, path=fixture_path)
    assert run.ran
    assert run.total == 3
    assert run.resolved == 2
    assert run.patches_generated == 2
    # fixture-3 ships no gold patch, so the oracle produces none for it.
    assert run.outcomes[2].status == "no patch"
    assert run.resolved_rate == round(2 / 3, 4)


async def test_a_partial_pass_shows_up_as_partial_in_the_run(
    fixture_path: Path, tmp_path: Path
) -> None:
    harness = SWEBenchHarness(
        patch_runner=oracle_patch_runner(),
        evaluator=_first_test_only,
        workspace=lambda task: tmp_path,
    )
    run = await run_or_report(harness, path=fixture_path, ids=["ronin__fixture-1"])
    assert run.resolved == 0
    assert run.partial == ("ronin__fixture-1",)
    assert "partial" in render_markdown(run)


async def test_one_task_that_blows_up_does_not_kill_the_run(
    fixture_path: Path, tmp_path: Path
) -> None:
    async def explodes(task: SWEBenchTask, workdir: Path) -> PatchAttempt:
        if task.instance_id == "ronin__fixture-1":
            raise RuntimeError("checkout failed")
        return PatchAttempt(patch="--- a\n+++ b\n")

    harness = SWEBenchHarness(
        patch_runner=explodes, evaluator=_always_passes, workspace=lambda task: tmp_path
    )
    run = await run_or_report(harness, path=fixture_path)
    assert run.total == 3
    assert run.errored == 1
    assert "patch runner failed" in run.outcomes[0].error
    assert run.outcomes[1].resolved


async def test_an_evaluator_that_raises_is_that_task_s_error(
    fixture_path: Path, tmp_path: Path
) -> None:
    async def explodes(task: SWEBenchTask, patch: str, workdir: Path) -> TaskEvaluation:
        raise RuntimeError("no container for this repo")

    harness = SWEBenchHarness(
        patch_runner=oracle_patch_runner(), evaluator=explodes, workspace=lambda task: tmp_path
    )
    run = await run_or_report(harness, path=fixture_path, limit=1)
    assert run.outcomes[0].status == "error"
    assert "no container" in run.outcomes[0].error
    assert run.outcomes[0].patch_generated


async def test_a_task_with_no_patch_never_reaches_the_evaluator(tmp_path: Path) -> None:
    seen: list[str] = []

    async def spy(task: SWEBenchTask, patch: str, workdir: Path) -> TaskEvaluation:
        seen.append(task.instance_id)
        return TaskEvaluation()

    async def no_patch(task: SWEBenchTask, workdir: Path) -> PatchAttempt:
        return PatchAttempt(patch="   \n")

    harness = SWEBenchHarness(
        patch_runner=no_patch, evaluator=spy, workspace=lambda task: tmp_path
    )
    run = await harness.run(
        SWEBenchDataset((SWEBenchTask(instance_id="x-1", fail_to_pass=("f",)),))
    )
    assert seen == []
    assert run.outcomes[0].status == "no patch"


async def test_the_by_repo_breakdown_groups_on_the_instance_id_prefix(
    fixture_path: Path, tmp_path: Path
) -> None:
    harness = SWEBenchHarness(
        patch_runner=oracle_patch_runner(),
        evaluator=_always_passes,
        workspace=lambda task: tmp_path,
    )
    run = await run_or_report(harness, path=fixture_path)
    assert run.by_repo() == {"other__fixture": (0, 1), "ronin__fixture": (2, 2)}
    assert "### By repo" in render_markdown(run, include_repo_breakdown=True)


# --------------------------------------------------------------------------- #
# The prompt
# --------------------------------------------------------------------------- #


def test_the_prompt_leaks_neither_the_gold_patch_nor_the_test_names(
    fixture_path: Path,
) -> None:
    task = load_dataset(path=fixture_path).tasks[0]
    prompt = default_prompt(task)
    assert task.problem_statement in prompt
    assert "test_negatives" not in prompt
    assert "return a + b" not in prompt


def test_a_huge_problem_statement_is_truncated_with_the_cut_named() -> None:
    task = SWEBenchTask(instance_id="x-1", problem_statement="y" * 20_000)
    prompt = default_prompt(task)
    assert "problem statement truncated" in prompt
    assert "7999 more characters" in prompt or "8000 more characters" in prompt


# --------------------------------------------------------------------------- #
# "Not tuned on", as three mechanisms rather than a promise
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Result:
    """An agent result that *does* carry a transcript, to prove none of it escapes."""

    text: str = "done"
    exit_code: int = 0
    events: tuple[str, ...] = ("TurnStart", "ToolStart", "TurnEnd")


class _Agent:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.closed = False

    async def run(self, prompt: str) -> _Result:
        return self.result

    async def aclose(self) -> None:
        self.closed = True


def test_the_agent_seam_has_no_way_to_name_a_transcript() -> None:
    """Mechanism 1. The harness cannot retain what its protocol cannot mention."""
    assert {name for name in dir(AgentRunLike) if not name.startswith("_")} == {
        "text",
        "exit_code",
    }
    assert {name for name in dir(AgentLike) if not name.startswith("_")} == {"run", "aclose"}


def test_no_field_of_a_recorded_value_can_hold_a_transcript() -> None:
    scalars = {"str", "int", "float", "bool", "str | None"}
    for cls in (PatchAttempt, SWEBenchOutcome):
        assert {f.type for f in fields(cls)} <= scalars, cls.__name__


async def test_the_adapter_always_opens_the_agent_with_recording_off(tmp_path: Path) -> None:
    """Mechanism 2. A recorded session is a ``.ronin/sessions/<id>.jsonl`` transcript,
    and a transcript on disk is a training row waiting to happen."""
    asked: list[bool] = []
    agent = _Agent(_Result())

    async def opener(path: Path, *, record: bool) -> _Agent:
        asked.append(record)
        return agent

    async def capture(workdir: Path) -> str:
        return "--- a\n+++ b\n"

    runner = agent_patch_runner(opener, capture)
    attempt = await runner(SWEBenchTask(instance_id="x-1"), tmp_path)
    assert asked == [False]
    assert agent.closed
    assert attempt.patch == "--- a\n+++ b\n"


def test_the_adapter_exposes_no_parameter_that_could_turn_recording_back_on() -> None:
    assert "record" not in inspect.signature(agent_patch_runner).parameters


async def test_a_run_serializes_no_part_of_the_agent_s_transcript(tmp_path: Path) -> None:
    agent = _Agent(_Result())

    async def opener(path: Path, *, record: bool) -> _Agent:
        return agent

    async def capture(workdir: Path) -> str:
        return "--- a\n+++ b\n"

    harness = SWEBenchHarness(
        patch_runner=agent_patch_runner(opener, capture),
        evaluator=_always_passes,
        workspace=lambda task: tmp_path,
    )
    run = await harness.run(
        SWEBenchDataset((SWEBenchTask(instance_id="x-1", fail_to_pass=("f",)),))
    )
    payload = json.dumps(run.to_json_dict())
    for forbidden in ("events", "TurnStart", "ToolStart", "messages", "conversation"):
        assert forbidden not in payload
    assert run.to_json_dict()["tuning_forbidden"] is True
    assert run.to_json_dict()["transcripts_retained"] is False


async def test_the_agent_s_exit_code_is_reported_as_a_note_not_as_a_transcript(
    tmp_path: Path,
) -> None:
    async def opener(path: Path, *, record: bool) -> _Agent:
        return _Agent(_Result(exit_code=2))

    async def no_change(workdir: Path) -> str:
        return ""

    attempt = await agent_patch_runner(opener, no_change)(
        SWEBenchTask(instance_id="x-1"), tmp_path
    )
    assert attempt.patch == ""
    assert "exited 2" in attempt.note
    assert "unchanged" in attempt.note


def test_a_report_states_the_disclaimer_and_offers_no_way_to_suppress_it() -> None:
    run = SWEBenchRun(outcomes=(SWEBenchOutcome(instance_id="x-1", resolved=True),))
    assert SWEBENCH_DISCLAIMER in render_markdown(run)
    assert "not a tuning target" in render_markdown(run)
    assert "suppress" not in inspect.signature(render_markdown).parameters


@pytest.mark.parametrize(
    "name",
    [
        "training/data/swebench.md",
        "out/sft/report.md",
        "x/dpo/report.json",
        "eval_snapshots/report.md",
    ],
)
def test_writing_a_report_into_a_training_path_is_refused(tmp_path: Path, name: str) -> None:
    """Mechanism 3, part one."""
    run = SWEBenchRun(outcomes=(SWEBenchOutcome(instance_id="x-1"),))
    with pytest.raises(TrainingSinkRefused, match="training directory"):
        write_report(run, tmp_path / name)


@pytest.mark.parametrize("suffix", [".jsonl", ".txt", ""])
def test_writing_a_report_as_anything_but_markdown_or_json_is_refused(
    tmp_path: Path, suffix: str
) -> None:
    """Mechanism 3, part two: JSONL is the training pipeline's row format."""
    run = SWEBenchRun(outcomes=(SWEBenchOutcome(instance_id="x-1"),))
    with pytest.raises(TrainingSinkRefused, match="allowed suffixes"):
        write_report(run, tmp_path / f"report{suffix}")


@pytest.mark.parametrize("suffix", REPORT_SUFFIXES)
def test_a_report_writes_as_markdown_and_as_json(tmp_path: Path, suffix: str) -> None:
    run = SWEBenchRun(outcomes=(SWEBenchOutcome(instance_id="x-1", resolved=True),))
    path = tmp_path / f"reports/swebench{suffix}"
    write_report(run, path)
    body = path.read_bytes().decode("utf-8")
    assert "tuning target" in body
    assert body.endswith("\n")


def test_a_did_not_run_report_written_as_json_carries_a_null_rate(tmp_path: Path) -> None:
    path = tmp_path / "swebench.json"
    write_report(did_not_run("no dataset in CI"), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["resolved_rate"] is None
    assert payload["resolved"] is None
    assert payload["ran"] is False
    assert payload["did_not_run"] == "no dataset in CI"


# --------------------------------------------------------------------------- #
# The one real subprocess: git, locally
# --------------------------------------------------------------------------- #


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "user.name=test",
            *args,
        ],
        check=True,
        capture_output=True,
    )


async def test_the_git_diff_capture_reports_what_the_agent_changed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "add.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    _git(repo, "add", "add.py")
    _git(repo, "commit", "-qm", "base")
    (repo / "add.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    diff = await git_diff_capture()(repo)
    assert "-    return a - b" in diff
    assert "+    return a + b" in diff


async def test_a_directory_git_cannot_read_yields_no_patch_rather_than_a_crash(
    tmp_path: Path,
) -> None:
    """"git could not tell us" and "the agent changed nothing" score identically —
    both are "no patch" — so inventing a distinction would invent information."""
    assert await git_diff_capture()(tmp_path) == ""
