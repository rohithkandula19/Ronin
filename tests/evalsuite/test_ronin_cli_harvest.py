"""``ronin harvest`` end to end: pool → run+record → read transcripts → datasets.

Lives in ``tests/evalsuite/`` for the same reason as ``test_ronin_cli_bench``:
``evals_harness`` is here and pytest's per-directory ``sys.path`` insertion does not
reach across test directories.

The model is always scripted, and the scripted agent here *writes a real transcript* —
because that transcript, not the returned events, is harvest's only input. So two things
are actually verified: that ``--dry-run`` reaches a task list with no provider, and that
the running path records trajectories, reads them back paired with each task's verify
result, and writes the three datasets plus the tagging summary.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import pytest
from evals_harness import PASSING_VERIFY, ScriptedRun, write_task

from ronin.cli.bench import Failed
from ronin.cli.harvest import (
    SUMMARY_FILE,
    HarvestOptions,
    container_command_runner,
    harvest_summary,
    is_eval_suite,
    outcomes_from_results,
    render_dry_run,
    run_harvest,
    select_pool,
)
from ronin.cli.spine import Paths
from ronin.core.types import (
    AgentState,
    Budget,
    Event,
    TextDelta,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.evals.adapters import OpenedAgent
from ronin.evals.harvest import HarvestResult, Rejection, Skip
from ronin.evals.task import EvalTask
from ronin.evals.taxonomy import AgentOutcome, TaskResult, VerifyProbe, judge
from ronin.persistence.transcript import Transcript, new_session_id, sessions_dir
from ronin.verify.runner import CommandOutcome

FIXED = "def add(a, b):\n    return a + b\n"


# --------------------------------------------------------------------------- #
# A scripted agent that records a transcript
# --------------------------------------------------------------------------- #


class RecordingAgent:
    """A fake agent that writes a real ``.ronin/sessions/<id>.jsonl`` and returns events.

    Unlike ``evals_harness.ScriptedAgent`` it persists the transcript, because harvest
    reads the transcript off disk — the returned events are what the runner folds for the
    taxonomy, but the *conversation* SFT trains on only exists in the recorded file.
    """

    def __init__(self, workspace: Path, *, edit_path: str, edit_content: str, text: str) -> None:
        self.workspace = workspace
        self._edit_path = edit_path
        self._edit_content = edit_content
        self._text = text
        self.closed = False

    @property
    def state(self) -> AgentState:
        return AgentState(budget=Budget())

    async def run(self, prompt: str, *, max_iterations: int = 25) -> ScriptedRun:
        target = self.workspace / self._edit_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self._edit_content, encoding="utf-8")
        events: tuple[Event, ...] = (
            TurnStart(turn_index=0),
            TextDelta(text=self._text),
            ToolStart(tool_use_id="c0", name="edit", arguments={"path": self._edit_path}),
            ToolEnd(
                tool_use_id="c0",
                name="edit",
                result=ToolResult(ok=True, content="ok", artifacts=(str(target),)),
            ),
            TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls"),
        )
        tx = Transcript.open(
            sessions_dir(self.workspace),
            new_session_id(clock=lambda: 0.0, entropy=lambda n: "abcdef"[:n]),
            model="fake",
            cwd=str(self.workspace),
        )
        for event in events:
            tx.append(event)
        tx.close()
        return ScriptedRun(text=self._text, events=events)

    async def aclose(self) -> None:
        self.closed = True


def recording_factory(
    *, edit_path: str = "app.py", edit_content: str = FIXED, text: str = "fixed the bug"
) -> Callable[[EvalTask, Path], Awaitable[OpenedAgent]]:
    async def factory(task: EvalTask, workspace: Path) -> OpenedAgent:
        agent = RecordingAgent(workspace, edit_path=edit_path, edit_content=edit_content, text=text)
        return OpenedAgent(agent=agent, registered_tools=("edit", "read", "bash"))

    return factory


def build_pool(root: Path) -> None:
    """One solvable task whose verify passes once run."""
    write_task(
        root,
        "fix-add",
        category="single-file",
        prompt="make add return the sum",
        fixture={"app.py": "def add(a, b):\n    return a - b\n"},
        solution={"app.py": FIXED},
        verify=PASSING_VERIFY,
        files_expected=["app.py"],
    )


def options(tasks: Path, out: Path, **kwargs: object) -> HarvestOptions:
    return HarvestOptions(tasks=tasks, out=out, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Selecting the pool
# --------------------------------------------------------------------------- #


def test_the_eval_suite_is_refused_as_harvest_input(tmp_path: Path) -> None:
    suite = tmp_path / "tests" / "evals"
    suite.mkdir(parents=True)
    result = select_pool(options(suite, tmp_path / "out"))
    assert isinstance(result, Failed)
    assert "eval suite" in result.message


def test_is_eval_suite_matches_the_tree_at_any_depth(tmp_path: Path) -> None:
    assert is_eval_suite(tmp_path / "tests" / "evals")
    assert is_eval_suite(tmp_path / "tests" / "evals" / "edit")
    assert not is_eval_suite(tmp_path / "pool")
    assert not is_eval_suite(tmp_path / "my" / "evals")  # needs the 'tests' segment too


def test_a_missing_pool_is_a_named_failure(tmp_path: Path) -> None:
    result = select_pool(options(tmp_path / "absent", tmp_path / "out"))
    assert isinstance(result, Failed)
    assert "no task pool" in result.message


def test_an_empty_pool_is_a_named_failure(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    result = select_pool(options(pool, tmp_path / "out"))
    assert isinstance(result, Failed)
    assert "no task.toml" in result.message


def test_a_selection_matching_nothing_is_a_named_failure(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    build_pool(pool)
    result = select_pool(options(pool, tmp_path / "out", ids=frozenset({"nope"})))
    assert isinstance(result, Failed)
    assert "matched none" in result.message


def test_selection_narrows_to_the_chosen_task(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    build_pool(pool)
    chosen = select_pool(options(pool, tmp_path / "out", ids=frozenset({"fix-add"})))
    assert not isinstance(chosen, Failed)
    assert [task.id for task in chosen] == ["fix-add"]


# --------------------------------------------------------------------------- #
# Dry run
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_dry_run_lists_tasks_and_opens_no_model(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    build_pool(pool)
    code, out, _err = await run_harvest(
        options(pool, tmp_path / "out", dry_run=True), Paths.discover(tmp_path), {}
    )
    assert code == 0
    assert "fix-add" in out
    assert "nothing ran" in out
    assert not (tmp_path / "out").exists()  # a dry run writes nothing


def test_render_dry_run_names_the_output_and_container_state(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    build_pool(pool)
    chosen = select_pool(options(pool, tmp_path / "out", container=True))
    assert not isinstance(chosen, Failed)
    text = render_dry_run(chosen, options(pool, tmp_path / "out", container=True))
    assert "container=on" in text
    assert "sft.jsonl" in text


# --------------------------------------------------------------------------- #
# The container verify wrapper
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_container_command_runner_wraps_verify_in_docker(tmp_path: Path) -> None:
    seen: dict[str, tuple[str, ...]] = {}

    async def base(
        argv: Sequence[str], *, cwd: Path, timeout: float = 0.0, **_: object
    ) -> CommandOutcome:
        seen["argv"] = tuple(argv)
        return CommandOutcome(argv=tuple(argv), exit_code=0, output="")

    runner = container_command_runner(base, image="python:3.11-slim")
    await runner(["bash", "verify.sh"], cwd=tmp_path, timeout=5.0)
    argv = seen["argv"]
    assert argv[:4] == ("docker", "run", "--rm", "--network=none")
    assert argv[-2:] == ("bash", "verify.sh")
    assert "python:3.11-slim" in argv


@pytest.mark.asyncio
async def test_container_without_docker_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = tmp_path / "pool"
    build_pool(pool)
    monkeypatch.setattr("ronin.cli.harvest.shutil.which", lambda _name: None)
    code, _out, err = await run_harvest(
        options(pool, tmp_path / "out", models=("m",), container=True),
        Paths.discover(tmp_path),
        {},
    )
    assert code == 1
    assert "docker" in err
    assert not (tmp_path / "out").exists()


# --------------------------------------------------------------------------- #
# Reading transcripts back
# --------------------------------------------------------------------------- #


def _result(workspace: Path, *, skipped: bool = False, verify_ok: bool = True) -> TaskResult:
    record = judge_stub(workspace, skipped=skipped, verify_ok=verify_ok)
    return record


def judge_stub(workspace: Path, *, skipped: bool, verify_ok: bool) -> TaskResult:
    from ronin.evals.taxonomy import RunRecord

    return judge(
        RunRecord(
            task_id="fix-add",
            category="single-file",
            prompt="make add return the sum",
            files_expected=("app.py",),
            outcome=AgentOutcome(registered_tools=("edit",)),
            verify_after=VerifyProbe(ran=True, exit_code=0 if verify_ok else 1),
            skipped=skipped,
            skip_reason="needs network" if skipped else "",
            workspace=str(workspace),
        )
    )


def test_a_skipped_run_is_noted_not_harvested(tmp_path: Path) -> None:
    notes: list[str] = []
    outcomes = outcomes_from_results([_result(tmp_path, skipped=True)], notes=notes)
    assert outcomes == []
    assert any("skipped" in note for note in notes)


def test_a_run_without_a_transcript_is_noted(tmp_path: Path) -> None:
    # A workspace with a work/ dir but no recorded session.
    (tmp_path / "work").mkdir()
    notes: list[str] = []
    outcomes = outcomes_from_results([_result(tmp_path)], notes=notes)
    assert outcomes == []
    assert any("no transcript" in note for note in notes)


def test_a_recorded_run_folds_into_an_outcome(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    tx = Transcript.open(sessions_dir(work), "s1", model="fake", cwd=str(work))
    tx.append(TurnStart(turn_index=0))
    tx.append(TextDelta(text="done"))
    tx.append(TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls"))
    tx.close()

    notes: list[str] = []
    outcomes = outcomes_from_results([_result(tmp_path, verify_ok=True)], notes=notes)
    assert notes == []
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.task_id == "fix-add"
    assert outcome.verify_passed is True
    # harvest prepends the prompt as a user turn when the transcript has none.
    assert any(message.role.value == "user" for message in outcome.messages)


# --------------------------------------------------------------------------- #
# The tagging summary
# --------------------------------------------------------------------------- #


def test_harvest_summary_tags_every_disposition() -> None:
    result = HarvestResult(
        sft=({"task_id": "a"},),
        accepted=("a",),
        rejections=(
            Rejection(task_id="b", reason="verify did not pass", failure_class="wrong_file"),
        ),
        skips=(Skip(task_id="c", reason="unreadable"),),
        category_medians={"edit": 3.0},
    )
    summary = harvest_summary(result, notes=["c: unreadable transcript"])
    assert summary["counts"]["accepted"] == 1
    assert summary["tags"]["a"]["disposition"] == "accepted"
    assert summary["tags"]["b"] == {
        "disposition": "rejected",
        "reason": "verify did not pass",
        "failure_class": "wrong_file",
    }
    assert summary["tags"]["c"]["disposition"] == "skipped"
    assert summary["notes"] == ["c: unreadable transcript"]


# --------------------------------------------------------------------------- #
# The full running path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_harvest_writes_datasets_and_summary(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    build_pool(pool)
    out = tmp_path / "out"
    code, stdout, stderr = await run_harvest(
        options(pool, out),
        Paths.discover(tmp_path),
        {},
        factory=recording_factory(),
    )
    assert code == 0, stderr
    assert "harvested 1 run(s)" in stdout
    for name in ("sft.jsonl", "dpo.jsonl", "rlvr.jsonl", SUMMARY_FILE):
        assert (out / name).is_file(), name

    # The solved task cleared the quality bar, so it is an SFT record.
    sft_rows = [json.loads(line) for line in (out / "sft.jsonl").read_text().splitlines()]
    assert len(sft_rows) == 1
    assert sft_rows[0]["task_id"] == "fix-add"
    assert sft_rows[0]["assistant_spans"], "the assistant turn must be a training target"

    summary = json.loads((out / SUMMARY_FILE).read_text())
    assert summary["counts"]["accepted"] == 1
    assert summary["tags"]["fix-add"]["disposition"] == "accepted"


@pytest.mark.asyncio
async def test_run_harvest_refuses_more_than_one_model(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    build_pool(pool)
    code, _out, err = await run_harvest(
        options(pool, tmp_path / "out", models=("a", "b")),
        Paths.discover(tmp_path),
        {},
        factory=recording_factory(),
    )
    assert code == 1
    assert "one model" in err
