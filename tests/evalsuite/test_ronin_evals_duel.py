"""``ronin.evals.duel`` — canonical transcripts, first divergence, and a scoreboard
that reproduces from its seed.

Every model here is a scripted fake and every path is under ``tmp_path``: nothing in
this module opens a socket, reads an API key, or spends a cent.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from duel_harness import (
    Call,
    FakeRecord,
    ScriptedTask,
    scripted_factory,
    table_script,
    turn,
    workdirs,
)

from ronin.core.types import Error, StreamReset, TextDelta, TurnEnd, TurnStart, TurnState
from ronin.evals.duel import (
    DEFAULT_MAX_DIFF_LINES,
    Duel,
    SideOutcome,
    TaskDuel,
    Verdict,
    diff_transcripts,
    render_duel_markdown,
    render_scoreboard,
    render_transcript_diff,
    run_duel,
    scoreboard,
    task_seed,
    transcript_lines,
)

# --------------------------------------------------------------------------- #
# Canonical transcript lines
# --------------------------------------------------------------------------- #


def test_text_deltas_are_coalesced_into_one_line_per_span() -> None:
    lines = transcript_lines(turn(0, text="hello world"))
    assert lines == (
        "turn 0 start",
        "text: hello world",
        "turn 0 end done (complete)",
    )


def test_thinking_is_labelled_separately_from_the_answer() -> None:
    lines = transcript_lines(turn(0, thinking="ponder", text="answer"))
    assert lines[1] == "think: ponder"
    assert lines[2] == "text: answer"


def test_tool_call_ids_are_renumbered_so_a_fresh_id_is_not_a_divergence() -> None:
    """Two runs that did the same thing must compare equal.

    Provider-generated ids are unique per call, so comparing them finds a difference
    on the first tool call of every run — the exact noise this renumbering removes.
    """
    left = turn(0, calls=[Call(id="toolu_aaa", name="read", arguments={"path": "a.py"})])
    right = turn(0, calls=[Call(id="toolu_zzz", name="read", arguments={"path": "a.py"})])
    assert transcript_lines(left) == transcript_lines(right)
    assert "call#1 read" in transcript_lines(left)[1]


def test_tool_arguments_are_sorted_so_key_order_cannot_fake_a_divergence() -> None:
    left = turn(0, calls=[Call(id="1", name="edit", arguments={"a": 1, "b": 2})])
    right = turn(0, calls=[Call(id="1", name="edit", arguments={"b": 2, "a": 1})])
    assert transcript_lines(left) == transcript_lines(right)


def test_a_failed_tool_result_renders_its_error_not_its_content() -> None:
    lines = transcript_lines(
        turn(0, calls=[Call(id="1", name="bash", ok=False, body="exit 127: no such file")])
    )
    assert any("-> error exit 127: no such file" in line for line in lines)


def test_a_stream_reset_retracts_prose_but_leaves_the_tool_call_that_already_ran() -> None:
    """``StreamReset``'s documented rule: a tool that ran cannot be un-run."""
    events = (
        TurnStart(turn_index=0),
        TextDelta(text="first attempt"),
        *turn(0, calls=[Call(id="1", name="read")])[1:-1],
        StreamReset(reason="provider retry"),
        TextDelta(text="second attempt"),
        TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="complete"),
    )
    lines = transcript_lines(events)
    assert "text: first attempt" not in lines
    assert "text: second attempt" in lines
    assert any("call#1 read" in line for line in lines)
    assert "reset (provider retry)" in lines


def test_a_long_line_is_clipped_with_the_cut_named() -> None:
    lines = transcript_lines(turn(0, text="x" * 500), )
    body = next(line for line in lines if line.startswith("text: "))
    assert body.endswith("chars)")
    assert "(+" in body


def test_an_error_event_renders_its_kind_and_recoverability() -> None:
    lines = transcript_lines((Error(message="boom", kind="provider", recoverable=True),))
    assert lines == ("error (provider, recoverable) boom",)


# --------------------------------------------------------------------------- #
# The transcript diff
# --------------------------------------------------------------------------- #


def test_identical_streams_produce_no_divergence() -> None:
    events = turn(0, text="same", calls=[Call(id="1", name="read")])
    diff = diff_transcripts(events, events)
    assert diff.identical
    assert diff.first_divergence is None
    assert all(line.startswith("  ") for line in diff.lines)


def test_the_diff_names_the_first_divergence_and_not_a_later_one() -> None:
    left = turn(0, text="same", calls=[Call(id="1", name="read"), Call(id="2", name="edit")])
    right = turn(0, text="same", calls=[Call(id="1", name="grep"), Call(id="2", name="write")])
    diff = diff_transcripts(left, right)
    assert diff.first_divergence is not None
    assert "read" in diff.first_divergence.a_line
    assert "grep" in diff.first_divergence.b_line
    assert "edit" not in diff.first_divergence.summary
    assert "A: " in diff.first_divergence.summary


def test_an_extra_step_on_one_side_is_reported_as_that_side_doing_something_alone() -> None:
    left = turn(0, text="hi", calls=[Call(id="1", name="read")])
    right = turn(0, text="hi")
    diff = diff_transcripts(left, right)
    assert diff.first_divergence is not None
    assert diff.first_divergence.kind == "delete"
    assert "A did something B never did" in diff.first_divergence.summary


def test_a_truncated_diff_keeps_the_window_around_the_divergence_and_marks_both_cuts() -> None:
    """Head-truncation would throw away the only interesting part of a long diff."""
    # Four identical turns, then one that differs — so the divergence is deep enough
    # that a head-truncating window would miss it entirely.
    common = tuple(event for n in range(4) for event in turn(n, text=f"step {n}"))
    left = (*common, *turn(4, calls=[Call(id="1", name="read")]))
    right = (*common, *turn(4, calls=[Call(id="1", name="grep")]))
    diff = diff_transcripts(left, right, max_lines=4)
    assert diff.omitted_head > 0
    assert diff.omitted_tail > 0
    assert any(line.startswith("... ") and "earlier" in line for line in diff.lines)
    assert any(line.startswith("... ") and "later" in line for line in diff.lines)
    kept = [line for line in diff.lines if not line.startswith("... ")]
    assert len(kept) == 4
    assert any("read" in line for line in kept)


def test_the_divergence_survives_truncation_even_when_its_line_is_cut() -> None:
    left = turn(0, text="a", calls=[Call(id="1", name="read")])
    right = turn(0, text="b", calls=[Call(id="1", name="read")])
    diff = diff_transcripts(left, right, max_lines=1)
    assert diff.first_divergence is not None
    assert diff.first_divergence.a_line == "text: a"


def test_rendering_a_diff_names_the_divergence_above_the_block() -> None:
    left = turn(0, calls=[Call(id="1", name="read")])
    right = turn(0, calls=[Call(id="1", name="grep")])
    rendered = render_transcript_diff(
        diff_transcripts(left, right), duelists=("alpha", "beta")
    )
    assert "First divergence" in rendered
    assert "`A` = alpha" in rendered
    assert "```text" in rendered


def test_rendering_identical_transcripts_says_so_instead_of_an_empty_block() -> None:
    events = turn(0, text="same")
    rendered = render_transcript_diff(diff_transcripts(events, events), duelists=("a", "b"))
    assert "identical" in rendered
    assert "```" not in rendered


# --------------------------------------------------------------------------- #
# Seeds
# --------------------------------------------------------------------------- #


def test_the_same_task_seed_is_derived_every_time() -> None:
    assert task_seed(7, "eval-1") == task_seed(7, "eval-1")


@pytest.mark.parametrize(
    ("seed", "task_id", "other_seed", "other_task"),
    [
        (1, "eval-1", 2, "eval-1"),
        (1, "eval-1", 1, "eval-2"),
    ],
)
def test_a_different_seed_or_task_derives_a_different_task_seed(
    seed: int, task_id: str, other_seed: int, other_task: str
) -> None:
    assert task_seed(seed, task_id) != task_seed(other_seed, other_task)


async def test_both_sides_are_built_with_the_same_per_task_seed(tmp_path: Path) -> None:
    """"Same seed" has to mean the two sides sampled the same way, or the A/B is a lie."""
    tasks = [ScriptedTask(id="t1"), ScriptedTask(id="t2")]
    seen: list[tuple[str, int]] = []
    script = table_script(
        {(side, task.id): FakeRecord(passed=True) for side in ("alpha", "beta") for task in tasks}
    )
    await run_duel(
        tasks,
        factory=scripted_factory(script, seen=seen),
        duelists=("alpha", "beta"),
        seed=99,
        workdir=workdirs(tmp_path),
    )
    per_task = [seen[i : i + 2] for i in range(0, len(seen), 2)]
    assert [pair[0][1] for pair in per_task] == [pair[1][1] for pair in per_task]
    assert [pair[0][1] for pair in per_task] == [task_seed(99, "t1"), task_seed(99, "t2")]


# --------------------------------------------------------------------------- #
# The scoreboard
# --------------------------------------------------------------------------- #


def _duel(*rows: TaskDuel, seed: int = 1) -> Duel:
    return Duel(seed=seed, duelists=("alpha", "beta"), tasks=rows)


def _side(name: str, *, passed: bool, taxonomy: tuple[str, ...] = ()) -> SideOutcome:
    return SideOutcome(duelist=name, passed=passed, taxonomy=taxonomy)


def test_a_duel_refuses_two_results_for_one_task() -> None:
    row = TaskDuel(
        task_id="t1", a=_side("alpha", passed=True), b=_side("beta", passed=False)
    )
    with pytest.raises(ValueError, match="same task id"):
        _duel(row, row)


@pytest.mark.parametrize(
    ("a_passed", "b_passed", "expected"),
    [
        (True, False, Verdict.A_WINS),
        (False, True, Verdict.B_WINS),
        (True, True, Verdict.TIE),
        (False, False, Verdict.TIE),
    ],
)
def test_the_verdict_follows_the_pass_flags(
    a_passed: bool, b_passed: bool, expected: Verdict
) -> None:
    row = TaskDuel(
        task_id="t1", a=_side("alpha", passed=a_passed), b=_side("beta", passed=b_passed)
    )
    assert row.verdict is expected


def test_both_failed_differently_needs_disagreeing_taxonomies() -> None:
    same = TaskDuel(
        task_id="t1",
        a=_side("alpha", passed=False, taxonomy=("tool_syntax",)),
        b=_side("beta", passed=False, taxonomy=("tool_syntax",)),
    )
    different = TaskDuel(
        task_id="t2",
        a=_side("alpha", passed=False, taxonomy=("tool_syntax",)),
        b=_side("beta", passed=False, taxonomy=("gate_violation",)),
    )
    assert not same.both_failed_differently
    assert same.both_failed_alike
    assert different.both_failed_differently
    assert not different.both_failed_alike


def test_taxonomy_order_is_not_a_difference() -> None:
    """The classifier's ordering is an artefact; treating it as a finding would put
    noise in the one column that is supposed to answer harness-versus-model."""
    row = TaskDuel(
        task_id="t1",
        a=_side("alpha", passed=False, taxonomy=("a", "b")),
        b=_side("beta", passed=False, taxonomy=("b", "a")),
    )
    assert not row.both_failed_differently


async def test_wins_losses_and_ties_add_up_to_the_task_count(tmp_path: Path) -> None:
    tasks = [ScriptedTask(id=f"t{n}") for n in range(1, 5)]
    table = {
        ("alpha", "t1"): FakeRecord(passed=True),
        ("beta", "t1"): FakeRecord(passed=False, taxonomy=("tool_syntax",)),
        ("alpha", "t2"): FakeRecord(passed=False, taxonomy=("gate_violation",)),
        ("beta", "t2"): FakeRecord(passed=True),
        ("alpha", "t3"): FakeRecord(passed=True),
        ("beta", "t3"): FakeRecord(passed=True),
        ("alpha", "t4"): FakeRecord(passed=False, taxonomy=("tool_syntax",)),
        ("beta", "t4"): FakeRecord(passed=False, taxonomy=("no_progress",)),
    }
    duel = await run_duel(
        tasks,
        factory=scripted_factory(table_script(table)),
        duelists=("alpha", "beta"),
        seed=5,
        workdir=workdirs(tmp_path),
    )
    board = scoreboard(duel)
    assert board.tasks == len(tasks)
    assert board.wins + board.losses + board.ties == len(tasks)
    assert (board.wins, board.losses, board.ties) == (1, 1, 2)


async def test_the_both_failed_differently_column_is_populated(tmp_path: Path) -> None:
    """The column the duel exists for: two models, same task, different failure."""
    tasks = [ScriptedTask(id="t1"), ScriptedTask(id="t2")]
    table = {
        ("alpha", "t1"): FakeRecord(passed=False, taxonomy=("tool_syntax",)),
        ("beta", "t1"): FakeRecord(passed=False, taxonomy=("gate_violation",)),
        ("alpha", "t2"): FakeRecord(passed=False, taxonomy=("no_progress",)),
        ("beta", "t2"): FakeRecord(passed=False, taxonomy=("no_progress",)),
    }
    duel = await run_duel(
        tasks,
        factory=scripted_factory(table_script(table)),
        duelists=("alpha", "beta"),
        seed=5,
        workdir=workdirs(tmp_path),
    )
    board = scoreboard(duel)
    assert board.both_failed_differently == ("t1",)
    assert board.both_failed_alike == ("t2",)
    rendered = render_scoreboard(board)
    assert "both failed, for different reasons" in rendered
    assert "tool_syntax" in rendered
    assert "gate_violation" in rendered


def test_a_scoreboard_with_no_such_task_says_none_rather_than_leaving_it_blank() -> None:
    board = scoreboard(
        _duel(TaskDuel(task_id="t1", a=_side("alpha", passed=True), b=_side("beta", passed=True)))
    )
    assert board.both_failed_differently == ()
    assert "None — no task was failed by both sides" in render_scoreboard(board)


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


async def _run(tmp_path: Path, seed: int, *, wall: list[float] | None = None) -> Duel:
    tasks = [ScriptedTask(id="t1"), ScriptedTask(id="t2")]
    calls = iter(wall or [])

    def script(duelist: str, task_id: str, task_level_seed: int) -> FakeRecord:
        passed = (duelist == "alpha") is (task_id == "t1")
        return FakeRecord(
            passed=passed,
            turns=2,
            tokens=100,
            cost_usd=0.02,
            wall_seconds=next(calls, 1.0),
            taxonomy=() if passed else (f"{duelist}_reason",),
            events=turn(0, text=f"{duelist} on {task_id}", calls=[Call(id="1", name="read")]),
        )

    return await run_duel(
        tasks,
        factory=scripted_factory(script),
        duelists=("alpha", "beta"),
        seed=seed,
        workdir=workdirs(tmp_path / str(seed)),
    )


async def test_two_duels_at_the_same_seed_render_a_byte_identical_scoreboard(
    tmp_path: Path,
) -> None:
    first = render_scoreboard(scoreboard(await _run(tmp_path / "one", 42)))
    second = render_scoreboard(scoreboard(await _run(tmp_path / "two", 42)))
    assert first.encode("utf-8") == second.encode("utf-8")


async def test_the_scoreboard_ignores_wall_clock_so_a_slow_machine_is_not_a_difference(
    tmp_path: Path,
) -> None:
    """Wall time is a property of the machine, not the seed. In the scoreboard it
    would make the determinism check flake on real hardware; it belongs in the
    human-readable report, and this test pins both halves of that."""
    fast = await _run(tmp_path / "fast", 42, wall=[0.1, 0.2, 0.3, 0.4])
    slow = await _run(tmp_path / "slow", 42, wall=[9.1, 9.2, 9.3, 9.4])
    assert render_scoreboard(scoreboard(fast)).encode() == render_scoreboard(
        scoreboard(slow)
    ).encode()
    assert render_duel_markdown(fast) != render_duel_markdown(slow)
    assert "0.10s" in render_duel_markdown(fast)


async def test_a_different_seed_is_allowed_to_produce_a_different_scoreboard(
    tmp_path: Path,
) -> None:
    tasks = [ScriptedTask(id="t1")]

    def script(duelist: str, task_id: str, task_level_seed: int) -> FakeRecord:
        return FakeRecord(passed=task_level_seed % 2 == 0, taxonomy=("odd",))

    async def board_for(seed: int) -> tuple[object, ...]:
        duel = await run_duel(
            tasks,
            factory=scripted_factory(script),
            duelists=("alpha", "beta"),
            seed=seed,
            workdir=workdirs(tmp_path / str(seed)),
        )
        return scoreboard(duel).rows

    assert await board_for(1) != await board_for(2)


# --------------------------------------------------------------------------- #
# Integration: real objects, only the model faked
# --------------------------------------------------------------------------- #


async def test_a_whole_duel_renders_a_report_with_a_scoreboard_and_a_diff(
    tmp_path: Path,
) -> None:
    tasks = [ScriptedTask(id="refactor-1"), ScriptedTask(id="gate-1")]
    table = {
        ("alpha", "refactor-1"): FakeRecord(
            passed=True,
            events=turn(0, text="reading", calls=[Call(id="a1", name="read")]),
        ),
        ("beta", "refactor-1"): FakeRecord(
            passed=False,
            taxonomy=("wrong_tool",),
            events=turn(0, text="reading", calls=[Call(id="b1", name="grep")]),
        ),
        ("alpha", "gate-1"): FakeRecord(
            passed=False,
            taxonomy=("gate_violation",),
            events=turn(0, text="patching", calls=[Call(id="a2", name="edit")]),
        ),
        ("beta", "gate-1"): FakeRecord(
            passed=False,
            taxonomy=("no_progress",),
            events=turn(0, text="patching"),
        ),
    }
    duel = await run_duel(
        tasks,
        factory=scripted_factory(table_script(table)),
        duelists=("alpha", "beta"),
        seed=11,
        workdir=workdirs(tmp_path),
        max_diff_lines=DEFAULT_MAX_DIFF_LINES,
    )
    report = render_duel_markdown(duel)
    assert "duel — alpha vs beta (seed 11)" in report
    assert "`refactor-1`" in report
    assert "First divergence" in report
    assert "gate_violation" in report
    board = scoreboard(duel)
    assert (board.wins, board.losses, board.ties) == (1, 0, 1)
    assert board.both_failed_differently == ("gate-1",)


async def test_the_two_sides_never_share_a_working_directory(tmp_path: Path) -> None:
    """A's edits as B's starting state would make the duel measure order, not models."""
    tasks = [ScriptedTask(id="t1")]
    seen: list[Path] = []

    def script(duelist: str, task_id: str, task_level_seed: int) -> FakeRecord:
        return FakeRecord(passed=True)

    factory = scripted_factory(script)

    def spy_factory(duelist: str, seed: int) -> object:
        inner = factory(duelist, seed)

        async def run(task: object, workdir: Path) -> object:
            seen.append(workdir)
            return await inner(task, workdir)  # type: ignore[arg-type]

        return run

    await run_duel(
        tasks,
        factory=spy_factory,  # type: ignore[arg-type]
        duelists=("alpha", "beta"),
        seed=3,
        workdir=workdirs(tmp_path),
    )
    assert len(seen) == 2
    assert seen[0] != seen[1]
