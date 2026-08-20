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
    FakeAgent,
    FakeRun,
    duelist_factory,
    fake_suite,
    report,
    row,
    slower,
    task,
    turn,
)

from ronin.core.types import (
    Budget,
    Compaction,
    Error,
    StreamReset,
    TextDelta,
    TurnEnd,
    TurnStart,
    TurnState,
    VerifyResult,
)
from ronin.evals.adapters import OpenedAgent
from ronin.evals.duel import (
    DEFAULT_MAX_DIFF_LINES,
    Duel,
    TaskDuel,
    TranscriptRecorder,
    Verdict,
    diff_transcripts,
    duel_scoreboard,
    pair_reports,
    recording_v2_adapter,
    render_duel_markdown,
    render_duel_scoreboard,
    render_transcript_diff,
    run_duel,
    task_seed,
    transcript_lines,
)
from ronin.evals.taxonomy import AgentOutcome, FailureClass

# --------------------------------------------------------------------------- #
# Canonical transcript lines
# --------------------------------------------------------------------------- #


def test_text_deltas_are_coalesced_into_one_line_per_span() -> None:
    assert transcript_lines(turn(0, text="hello world")) == (
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

    Provider-generated ids are unique per call, so comparing them raw finds a
    difference on the first tool call of every run — the noise this removes.
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
    body = next(
        line for line in transcript_lines(turn(0, text="x" * 500)) if line.startswith("text: ")
    )
    assert "(+" in body
    assert body.endswith("chars)")


def test_an_error_event_renders_its_kind_and_recoverability() -> None:
    assert transcript_lines((Error(message="boom", kind="provider", recoverable=True),)) == (
        "error (provider, recoverable) boom",
    )


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


def test_an_extra_step_on_one_side_is_reported_as_that_side_acting_alone() -> None:
    diff = diff_transcripts(
        turn(0, text="hi", calls=[Call(id="1", name="read")]), turn(0, text="hi")
    )
    assert diff.first_divergence is not None
    assert diff.first_divergence.kind == "delete"
    assert "A did something B never did" in diff.first_divergence.summary


def test_a_truncated_diff_keeps_the_window_around_the_divergence_and_marks_both_cuts() -> None:
    """Head-truncation would throw away the only interesting part of a long diff."""
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
    diff = diff_transcripts(
        turn(0, text="a", calls=[Call(id="1", name="read")]),
        turn(0, text="b", calls=[Call(id="1", name="read")]),
        max_lines=1,
    )
    assert diff.first_divergence is not None
    assert diff.first_divergence.a_line == "text: a"


def test_rendering_a_diff_names_the_divergence_above_the_block() -> None:
    rendered = render_transcript_diff(
        diff_transcripts(
            turn(0, calls=[Call(id="1", name="read")]),
            turn(0, calls=[Call(id="1", name="grep")]),
        ),
        duelists=("alpha", "beta"),
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
    [(1, "eval-1", 2, "eval-1"), (1, "eval-1", 1, "eval-2")],
)
def test_a_different_seed_or_task_derives_a_different_task_seed(
    seed: int, task_id: str, other_seed: int, other_task: str
) -> None:
    assert task_seed(seed, task_id) != task_seed(other_seed, other_task)


# --------------------------------------------------------------------------- #
# The recording adapter — the one thing the eval runner does not keep
# --------------------------------------------------------------------------- #


async def test_the_recording_adapter_keeps_the_events_and_still_folds_them(
    tmp_path: Path,
) -> None:
    """``v2_adapter`` folds the stream and discards it, which is right for the taxonomy
    and wrong for a duel — the diff is the duel's whole added value."""
    events = turn(0, text="hi", calls=[Call(id="1", name="edit", arguments={"path": "a.py"})])
    agent = FakeAgent(run_result=FakeRun(text="hi", events=events), budget=Budget(spent_tokens=9))

    async def open_agent(eval_task: object, workspace: Path) -> OpenedAgent:
        return OpenedAgent(agent=agent, registered_tools=("edit",))

    recorder = TranscriptRecorder()
    outcome = await recording_v2_adapter(open_agent, recorder)(task("t1", root=tmp_path), tmp_path)
    assert recorder.stream_for("t1") == events
    assert outcome.final_text == "hi"
    assert outcome.turns_used == 1
    assert outcome.usage.reported is True
    assert agent.closed


async def test_an_agent_that_raises_records_an_empty_stream_rather_than_no_entry(
    tmp_path: Path,
) -> None:
    """A missing entry would silently compare the other side against absence."""
    agent = FakeAgent(run_result=FakeRun(), raises="no model configured")

    async def open_agent(eval_task: object, workspace: Path) -> OpenedAgent:
        return OpenedAgent(agent=agent)

    recorder = TranscriptRecorder()
    outcome = await recording_v2_adapter(open_agent, recorder)(task("t1", root=tmp_path), tmp_path)
    assert recorder.streams == {"t1": ()}
    assert "no model configured" in outcome.error
    assert agent.closed


def test_a_task_that_never_ran_has_an_empty_stream_not_a_key_error() -> None:
    assert TranscriptRecorder().stream_for("nope") == ()


# --------------------------------------------------------------------------- #
# Pairing two reports
# --------------------------------------------------------------------------- #


def test_a_duel_refuses_two_results_for_one_task() -> None:
    entry = TaskDuel(task_id="t1", a=row("t1"), b=row("t1", status="fail"))
    with pytest.raises(ValueError, match="two results for one task"):
        Duel(seed=1, duelists=("a", "b"), reports=(report("a"), report("b")), tasks=(entry, entry))


@pytest.mark.parametrize(
    ("a_status", "b_status", "expected"),
    [
        ("pass", "fail", Verdict.A_WINS),
        ("fail", "pass", Verdict.B_WINS),
        ("pass", "pass", Verdict.TIE),
        ("fail", "fail", Verdict.TIE),
        ("skip", "skip", Verdict.TIE),
        ("timeout", "error", Verdict.TIE),
    ],
)
def test_the_verdict_follows_the_status_cells(
    a_status: str, b_status: str, expected: Verdict
) -> None:
    entry = TaskDuel(task_id="t1", a=row("t1", status=a_status), b=row("t1", status=b_status))
    assert entry.verdict is expected


def test_a_skip_is_nobody_s_failure() -> None:
    """Counting a skip as a shared failure would put the harness in the dock for a
    task that never ran."""
    entry = TaskDuel(task_id="t1", a=row("t1", status="skip"), b=row("t1", status="skip"))
    assert not entry.both_failed
    assert not entry.both_failed_differently
    assert not entry.both_failed_alike


def test_both_failed_differently_needs_disagreeing_taxonomies() -> None:
    same = TaskDuel(
        task_id="t1",
        a=row("t1", status="fail", classes=(FailureClass.WRONG_FILE,)),
        b=row("t1", status="fail", classes=(FailureClass.WRONG_FILE,)),
    )
    different = TaskDuel(
        task_id="t2",
        a=row("t2", status="fail", classes=(FailureClass.WRONG_FILE,)),
        b=row("t2", status="fail", classes=(FailureClass.LOOPED,)),
    )
    assert not same.both_failed_differently
    assert same.both_failed_alike
    assert different.both_failed_differently
    assert not different.both_failed_alike


def test_taxonomy_order_is_not_a_difference() -> None:
    """The classifier's ranking is an artefact; treating it as a finding would put
    noise in the one column that must not have any."""
    entry = TaskDuel(
        task_id="t1",
        a=row("t1", status="fail", classes=(FailureClass.LOOPED, FailureClass.WRONG_FILE)),
        b=row("t1", status="fail", classes=(FailureClass.WRONG_FILE, FailureClass.LOOPED)),
    )
    assert not entry.both_failed_differently


def test_a_task_only_one_side_ran_is_named_rather_than_scored() -> None:
    """A comparison over a shifting task set is not a comparison."""
    duel = pair_reports(
        report("alpha", row("t1"), row("only-a")),
        report("beta", row("t1", status="fail")),
        seed=1,
    )
    assert [entry.task_id for entry in duel.tasks] == ["t1"]
    assert duel.incomparable == ("only-a",)
    board = duel_scoreboard(duel)
    assert board.tasks == 1
    assert board.incomparable == ("only-a",)
    assert "not run by both sides" in render_duel_scoreboard(board)


def test_the_duelist_names_default_to_the_report_labels() -> None:
    duel = pair_reports(report("alpha", row("t1")), report("beta", row("t1")), seed=3)
    assert duel.duelists == ("alpha", "beta")


def test_wins_losses_and_ties_add_up_to_the_task_count() -> None:
    duel = pair_reports(
        report(
            "alpha",
            row("t1"),
            row("t2", status="fail", classes=(FailureClass.LOOPED,)),
            row("t3"),
            row("t4", status="fail", classes=(FailureClass.WRONG_FILE,)),
        ),
        report(
            "beta",
            row("t1", status="fail", classes=(FailureClass.GAVE_UP_EARLY,)),
            row("t2"),
            row("t3"),
            row("t4", status="fail", classes=(FailureClass.LOOPED,)),
        ),
        seed=5,
    )
    board = duel_scoreboard(duel)
    assert board.tasks == 4
    assert board.wins + board.losses + board.ties == 4
    assert (board.wins, board.losses, board.ties) == (1, 1, 2)


def test_the_both_failed_differently_column_is_populated() -> None:
    """The column the duel exists for."""
    duel = pair_reports(
        report(
            "alpha",
            row("t1", status="fail", classes=(FailureClass.WRONG_FILE,)),
            row("t2", status="fail", classes=(FailureClass.LOOPED,)),
        ),
        report(
            "beta",
            row("t1", status="fail", classes=(FailureClass.GAVE_UP_EARLY,)),
            row("t2", status="fail", classes=(FailureClass.LOOPED,)),
        ),
        seed=5,
    )
    board = duel_scoreboard(duel)
    assert board.both_failed_differently == ("t1",)
    assert board.both_failed_alike == ("t2",)
    rendered = render_duel_scoreboard(board)
    assert "both failed, for different reasons" in rendered
    assert "wrong_file" in rendered
    assert "gave_up_early" in rendered


def test_a_scoreboard_with_no_such_task_says_none_rather_than_leaving_it_blank() -> None:
    board = duel_scoreboard(
        pair_reports(report("alpha", row("t1")), report("beta", row("t1")), seed=1)
    )
    assert board.both_failed_differently == ()
    assert "None — no task was failed by both sides" in render_duel_scoreboard(board)


def test_the_shared_comparison_is_the_eval_suite_s_own_scoreboard() -> None:
    """One implementation of "compare two runs" in the package, not two."""
    board = duel_scoreboard(
        pair_reports(
            report("alpha", row("t1"), row("t2", status="fail")),
            report("beta", row("t1", status="fail"), row("t2")),
            seed=1,
        )
    )
    assert board.shared.labels == ("alpha", "beta")
    assert board.shared.regressions == ("t1",)
    assert board.shared.fixes == ("t2",)
    rendered = render_duel_scoreboard(board)
    assert "# scoreboard" in rendered
    assert "regressions vs `alpha`" in rendered


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_the_scoreboard_ignores_wall_clock_so_a_slow_machine_is_not_a_difference() -> None:
    """Wall time is a property of the machine, not the seed. In the scoreboard it would
    make the determinism check flake on real hardware; it belongs in the readable
    report, and this pins both halves of that."""
    fast_a = report("alpha", row("t1", wall_seconds=0.1), row("t2", status="fail"))
    fast_b = report("beta", row("t1", status="fail"), row("t2", wall_seconds=0.2))
    fast = pair_reports(fast_a, fast_b, seed=42)
    slow = pair_reports(slower(fast_a, 40.0), slower(fast_b, 40.0), seed=42)

    assert render_duel_scoreboard(duel_scoreboard(fast)).encode("utf-8") == render_duel_scoreboard(
        duel_scoreboard(slow)
    ).encode("utf-8")
    assert render_duel_markdown(fast) != render_duel_markdown(slow)
    assert "0.10s" in render_duel_markdown(fast)


def _script(duelist: str, seed: int, eval_task: object) -> FakeRun:
    """Alpha passes t1 and fails t2; beta the reverse. Deterministic in the seed."""
    task_id = getattr(eval_task, "id", "")
    edits = ("target.py",) if (duelist == "alpha") is (task_id == "t1") else ("stray.py",)
    return FakeRun(
        text=f"{duelist} finished {task_id}",
        events=turn(
            0,
            text=f"{duelist} on {task_id}",
            calls=[Call(id="c1", name="edit", arguments={"path": edits[0]}, artifacts=edits)],
        ),
    )


async def _duel(root: Path, seed: int) -> Duel:
    tasks = [
        task("t1", root=root, files_expected=("target.py",)),
        task("t2", root=root, files_expected=("target.py",)),
    ]

    def gate(eval_task: object, outcome: AgentOutcome) -> tuple[int, bool]:
        return (0 if "target.py" in outcome.edited_paths else 1, False)

    return await run_duel(
        tasks,
        factory=duelist_factory(_script),
        suite=fake_suite(root, gate, walls=[0.5, 0.5, 0.5, 0.5]),
        duelists=("alpha", "beta"),
        seed=seed,
        max_diff_lines=DEFAULT_MAX_DIFF_LINES,
    )


async def test_two_duels_at_the_same_seed_render_a_byte_identical_scoreboard(
    tmp_path: Path,
) -> None:
    first = render_duel_scoreboard(duel_scoreboard(await _duel(tmp_path / "one", 42)))
    second = render_duel_scoreboard(duel_scoreboard(await _duel(tmp_path / "two", 42)))
    assert first.encode("utf-8") == second.encode("utf-8")


async def test_a_different_seed_is_allowed_to_produce_a_different_scoreboard(
    tmp_path: Path,
) -> None:
    def script(duelist: str, seed: int, eval_task: object) -> FakeRun:
        edited = "target.py" if seed % 2 == 0 else "stray.py"
        return FakeRun(
            events=turn(
                0,
                calls=[
                    Call(
                        id="c1",
                        name="edit",
                        arguments={"path": edited},
                        artifacts=(edited,),
                    )
                ],
            )
        )

    def gate(eval_task: object, outcome: AgentOutcome) -> tuple[int, bool]:
        return (0 if "target.py" in outcome.edited_paths else 1, False)

    async def board_for(seed: int) -> tuple[object, ...]:
        duel = await run_duel(
            [task("t1", root=tmp_path, files_expected=("target.py",))],
            factory=duelist_factory(script),
            suite=fake_suite(tmp_path / str(seed), gate, walls=[1.0, 1.0]),
            duelists=("alpha", "beta"),
            seed=seed,
        )
        return duel_scoreboard(duel).rows

    assert await board_for(task_seed_parity_even()) != await board_for(task_seed_parity_odd())


def task_seed_parity_even() -> int:
    """A duel seed whose derived per-task seed for ``t1`` is even."""
    return next(seed for seed in range(100) if task_seed(seed, "t1") % 2 == 0)


def task_seed_parity_odd() -> int:
    return next(seed for seed in range(100) if task_seed(seed, "t1") % 2 == 1)


async def test_both_sides_are_built_with_the_same_per_task_seed(tmp_path: Path) -> None:
    """ "Same seed" has to mean the two sides sampled the same way, or the A/B is a lie."""
    tasks = [task("t1", root=tmp_path), task("t2", root=tmp_path)]
    seen: list[tuple[str, int]] = []

    def gate(eval_task: object, outcome: AgentOutcome) -> tuple[int, bool]:
        return (0, False)

    await run_duel(
        tasks,
        factory=duelist_factory(_script, seen=seen),
        suite=fake_suite(tmp_path, gate, walls=[1.0] * 4),
        duelists=("alpha", "beta"),
        seed=99,
    )
    assert {name for name, _seed in seen} == {"alpha", "beta"}
    alpha = [seed for name, seed in seen if name == "alpha"]
    beta = [seed for name, seed in seen if name == "beta"]
    assert alpha == beta == [task_seed(99, "t1"), task_seed(99, "t2")]


# --------------------------------------------------------------------------- #
# Integration: real adapter, real taxonomy, real report — only the model faked
# --------------------------------------------------------------------------- #


async def test_a_whole_duel_runs_the_suite_twice_and_reports_a_scoreboard_and_a_diff(
    tmp_path: Path,
) -> None:
    duel = await _duel(tmp_path, 11)
    board = duel_scoreboard(duel)
    assert board.tasks == 2
    assert board.wins + board.losses + board.ties == 2
    assert (board.wins, board.losses) == (1, 1)

    report_text = render_duel_markdown(duel)
    assert "duel — alpha vs beta (seed 11)" in report_text
    assert "`t1`" in report_text
    assert "First divergence" in report_text
    # Both sides ran the same task with a different edit target, so the diff must find
    # the divergence in the tool arguments rather than reporting identical streams.
    assert all(not entry.diff.identical for entry in duel.tasks)


async def test_a_duel_where_both_sides_fail_differently_surfaces_that_from_real_findings(
    tmp_path: Path,
) -> None:
    """The taxonomy classes here come from ``judge``, not from a hand-written row."""

    def script(duelist: str, seed: int, eval_task: object) -> FakeRun:
        if duelist == "alpha":
            # Edits a file nobody asked for → WRONG_FILE.
            return FakeRun(
                events=turn(
                    0,
                    calls=[
                        Call(
                            id="c1",
                            name="edit",
                            arguments={"path": "stray.py"},
                            artifacts=("stray.py",),
                        )
                    ],
                )
            )
        # Says it is done having touched nothing → GAVE_UP_EARLY.
        return FakeRun(text="I believe this is already correct.", events=turn(0, text="nothing"))

    def gate(eval_task: object, outcome: AgentOutcome) -> tuple[int, bool]:
        return (1, False)

    duel = await run_duel(
        [task("t1", root=tmp_path, files_expected=("target.py",))],
        factory=duelist_factory(script),
        suite=fake_suite(tmp_path, gate, walls=[1.0, 1.0]),
        duelists=("alpha", "beta"),
        seed=7,
    )
    board = duel_scoreboard(duel)
    assert board.ties == 1
    entry = duel.tasks[0]
    assert entry.both_failed
    assert set(entry.a.classes) != set(entry.b.classes), (
        f"expected different taxonomy classes, got {entry.a.classes} and {entry.b.classes}"
    )
    assert board.both_failed_differently == ("t1",)
    assert "both failed, for different reasons" in render_duel_scoreboard(board)


def test_a_compaction_line_reports_both_token_estimates() -> None:
    """A duel comparing two models is exactly where "it compacted" without
    "by how much" misleads — the ratio is the only honest measure."""
    (line,) = transcript_lines(
        (Compaction(folded_messages=9, token_estimate_before=8000, token_estimate_after=1200),)
    )
    assert "folded 9 msg" in line
    assert "8000→1200" in line


def test_a_failed_summarizer_is_named_in_the_compaction_line() -> None:
    (line,) = transcript_lines((Compaction(folded_messages=2, summarizer_failed=True),))
    assert "summarizer failed" in line


def test_verify_skipped_reads_differently_from_verify_failed() -> None:
    (skipped,) = transcript_lines((VerifyResult(ran=False, summary="no test command"),))
    (failed,) = transcript_lines((VerifyResult(ran=True, passed=False, checks_failed=3),))
    assert "skipped" in skipped and "no test command" in skipped
    assert "failed" in failed and "3 failed" in failed
    assert skipped != failed


def test_a_repaired_pass_says_so() -> None:
    (line,) = transcript_lines(
        (VerifyResult(ran=True, passed=True, checks_passed=5, repaired=True),)
    )
    assert "passed" in line and "repaired" in line
