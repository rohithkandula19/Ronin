"""RE.4 acceptance: recorded runs become training-ready SFT / DPO / RLVR datasets.

The spec's required test is :func:`test_harvest_produces_a_filtered_training_ready_dataset`
— ten crafted runs in, three parseable datasets out, with no hand-editing: the quality
bar drops what it should, the SFT records are valid sharegpt with the assistant turns
marked, a DPO pair falls out of a passed+failed pair for one task, and the RLVR records
carry their reward. Everything else here holds one moving part still at a time.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from evals_harness import call

from ronin.core.types import (
    AgentState,
    Message,
    Role,
    Text,
    TextDelta,
    ToolEnd,
    ToolResult,
    ToolResultBlock,
    ToolStart,
    ToolUse,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.evals.harvest import (
    ASSISTANT_TAG,
    FROM_TAG,
    TaskOutcome,
    TrajectoryQualityBar,
    harvest,
    make_pair,
    outcome_from_transcript,
    render_tool_call,
    repeated_tool_errors,
    to_dpo,
    to_sharegpt,
    tool_validity,
    verifiable_reward,
    write_dataset,
)
from ronin.evals.taxonomy import ToolCallRecord
from ronin.persistence.transcript import ReadResult

# --------------------------------------------------------------------------- #
# Builders — a conversation and an outcome, from literals
# --------------------------------------------------------------------------- #


def convo(*, prompt: str = "Fix add() in app.py", final: str = "Done.") -> tuple[Message, ...]:
    """A standard system / user / assistant+tool / tool / assistant conversation."""
    return (
        Message(role=Role.SYSTEM, content_blocks=(Text("You are a coding agent."),)),
        Message(role=Role.USER, content_blocks=(Text(prompt),)),
        Message(
            role=Role.ASSISTANT,
            content_blocks=(
                Text("I'll edit it."),
                ToolUse(id="c1", name="edit", arguments={"path": "app.py"}),
            ),
        ),
        Message(
            role=Role.TOOL,
            content_blocks=(ToolResultBlock(tool_use_id="c1", content="edited"),),
        ),
        Message(role=Role.ASSISTANT, content_blocks=(Text(final),)),
    )


CLEAN: tuple[ToolCallRecord, ...] = (call("edit", ok=True), call("read", ok=True))
DOUBLE_ERROR: tuple[ToolCallRecord, ...] = (
    call("edit", ok=False, error="boom"),
    call("edit", ok=False, error="boom"),
)


def oc(
    task_id: str,
    *,
    verify: bool = True,
    turns: int = 4,
    tool_calls: Sequence[ToolCallRecord] = CLEAN,
    final: str = "Done.",
    category: str = "edit",
    prompt: str = "Fix add() in app.py",
) -> TaskOutcome:
    return TaskOutcome(
        task_id=task_id,
        category=category,
        prompt=prompt,
        verify_passed=verify,
        turns=turns,
        messages=convo(prompt=prompt, final=final),
        tool_calls=tuple(tool_calls),
    )


def ten_outcomes() -> list[TaskOutcome]:
    """Ten runs: six clean passes, one verify-fail, one over-median, one double-error, and
    a passed+failed pair sharing task id ``dup`` for DPO."""
    return [
        oc("t01"),
        oc("t02"),
        oc("t03", turns=3),
        oc("t04", verify=False),  # drop: verify did not pass
        oc("t05", turns=100),  # drop: turns far over the category median
        oc("t06", tool_calls=DOUBLE_ERROR),  # drop: same tool errored twice
        oc("t07", turns=5),
        oc("t08"),
        oc("dup", final="Fixed by returning a + b."),  # DPO chosen
        oc("dup", verify=False, final="You could try editing app.py yourself."),  # DPO rejected
    ]


# --------------------------------------------------------------------------- #
# The spec's required test
# --------------------------------------------------------------------------- #


def test_harvest_produces_a_filtered_training_ready_dataset(tmp_path: Path) -> None:
    result = harvest(ten_outcomes(), bar=TrajectoryQualityBar())

    # --- the quality bar drops exactly the three shapes it must, each for its reason ---
    assert result.accepted_count == 6
    assert result.rejected_count == 4
    assert result.skipped_count == 0
    reason_by_task = {rejection.task_id: rejection.reason for rejection in result.rejections}
    assert reason_by_task["t04"] == "verify did not pass"
    assert reason_by_task["dup"] == "verify did not pass"
    assert "exceed" in reason_by_task["t05"]
    assert "errored 2 times" in reason_by_task["t06"]
    # A drop that FAILED verify is labelled by the taxonomy (model/harness); a drop that
    # still passed verify — t06 floundered but finished — carries no failure class, because
    # it did not fail the task, it just took a bad path to solving it.
    t04_reject = next(r for r in result.rejections if r.task_id == "t04")
    assert t04_reject.failure_class  # the taxonomy had something to say
    dropped_t06 = next(r for r in result.rejections if r.task_id == "t06")
    assert dropped_t06.failure_class == ""

    # --- SFT: valid sharegpt, correct role tags, assistant spans marked ---
    assert len(result.sft) == 6
    sft_by_id = {record["task_id"]: record for record in result.sft}
    assert set(sft_by_id) == {"t01", "t02", "t03", "t07", "t08", "dup"}
    t01 = sft_by_id["t01"]
    tags = [turn["from"] for turn in t01["conversations"]]
    assert tags == ["system", "human", "gpt", "tool", "gpt"]
    assert t01["assistant_spans"] == [2, 4]
    # The assistant turn that called a tool renders the call as trainable text.
    assert "<tool_call>" in t01["conversations"][2]["value"]
    assert "edit" in t01["conversations"][2]["value"]

    # --- DPO: one pair, built from the passed (chosen) and failed (rejected) `dup` runs ---
    assert len(result.dpo) == 1
    pair = result.dpo[0]
    assert pair["task_id"] == "dup"
    assert pair["chosen"] == "Fixed by returning a + b."
    assert pair["rejected"] == "You could try editing app.py yourself."
    assert pair["prompt"]

    # --- RLVR: every non-skipped run, each carrying the reward and verify fields ---
    assert len(result.rlvr) == 10
    rlvr_keys = {"task_id", "prompt", "reward", "verify_passed", "turns", "tool_validity"}
    for record in result.rlvr:
        assert set(record) == rlvr_keys
    t06_rlvr = next(record for record in result.rlvr if record["task_id"] == "t06")
    assert t06_rlvr["reward"] == 1.0  # it did pass verify...
    assert t06_rlvr["tool_validity"] == 0.0  # ...but every tool call failed
    assert any(record["reward"] == 0.0 for record in result.rlvr)  # the verify-fails

    # --- write_dataset: three parseable jsonl files on disk ---
    paths = write_dataset(result, tmp_path / "dataset")
    assert set(paths) == {"sft", "dpo", "rlvr"}
    sft_lines = _read_jsonl(paths["sft"])
    dpo_lines = _read_jsonl(paths["dpo"])
    rlvr_lines = _read_jsonl(paths["rlvr"])
    assert len(sft_lines) == 6
    assert len(dpo_lines) == 1
    assert len(rlvr_lines) == 10
    assert sft_lines[0]["conversations"]  # round-trips as real records


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# --------------------------------------------------------------------------- #
# The masking-intent test
# --------------------------------------------------------------------------- #


def test_sharegpt_marks_assistant_turns_so_a_trainer_can_mask_the_rest() -> None:
    record = to_sharegpt(oc("mask"))
    conversations = record["conversations"]
    tags = [turn["from"] for turn in conversations]

    # Every role lands on a tag distinct from the assistant's, so the trainer can tell a
    # target turn from context. Training on tool results teaches hallucination; the mask
    # that prevents it is derivable from these tags alone.
    assert set(tags) == {"system", "human", "gpt", "tool"}
    assert record["assistant_spans"] == [i for i, tag in enumerate(tags) if tag == "gpt"]
    for index, turn in enumerate(conversations):
        is_assistant = index in record["assistant_spans"]
        assert (turn["from"] == "gpt") == is_assistant


# --------------------------------------------------------------------------- #
# The quality bar, one gate at a time
# --------------------------------------------------------------------------- #


def test_quality_bar_accepts_a_clean_passing_low_turn_run() -> None:
    assert TrajectoryQualityBar().accepts(oc("ok", turns=4), category_median_turns=4.0)


def test_quality_bar_rejects_each_failure_shape() -> None:
    bar = TrajectoryQualityBar()
    assert not bar.accepts(oc("v", verify=False), category_median_turns=4.0)
    assert not bar.accepts(oc("slow", turns=10), category_median_turns=4.0)
    assert not bar.accepts(oc("loop", tool_calls=DOUBLE_ERROR), category_median_turns=4.0)


def test_a_zero_median_disables_the_turns_gate_rather_than_rejecting_everything() -> None:
    # With no turn evidence there is nothing to be over, so the gate abstains.
    assert TrajectoryQualityBar().accepts(oc("x", turns=7), category_median_turns=0.0)


# --------------------------------------------------------------------------- #
# The derived signals
# --------------------------------------------------------------------------- #


def test_repeated_tool_errors_counts_the_same_failing_call() -> None:
    assert repeated_tool_errors(oc("a", tool_calls=DOUBLE_ERROR)) == 2
    assert repeated_tool_errors(oc("b", tool_calls=CLEAN)) == 0
    mixed = (call("edit", ok=False, error="x"), call("read", ok=False, error="y"))
    assert repeated_tool_errors(oc("c", tool_calls=mixed)) == 1  # two different failing calls


def test_tool_validity_is_the_fraction_that_succeeded() -> None:
    assert tool_validity(oc("a", tool_calls=CLEAN)) == 1.0
    assert tool_validity(oc("b", tool_calls=DOUBLE_ERROR)) == 0.0
    assert tool_validity(oc("c", tool_calls=())) == 1.0  # nothing ran, nothing failed


def test_verifiable_reward_is_binary_on_verify_unless_overridden() -> None:
    assert verifiable_reward(oc("pass", verify=True)) == 1.0
    assert verifiable_reward(oc("fail", verify=False)) == 0.0
    override = TaskOutcome(task_id="z", verify_passed=False, reward=0.25)
    assert verifiable_reward(override) == 0.25


# --------------------------------------------------------------------------- #
# DPO format builders
# --------------------------------------------------------------------------- #


def test_make_pair_and_to_dpo_produce_the_preference_record() -> None:
    pair = make_pair("prompt text", "the good answer", "the bad answer", task_id="t")
    assert to_dpo(pair) == {
        "task_id": "t",
        "prompt": "prompt text",
        "chosen": "the good answer",
        "rejected": "the bad answer",
    }


# --------------------------------------------------------------------------- #
# Errors are values — a malformed input is a skip, not a crash
# --------------------------------------------------------------------------- #


def test_an_outcome_with_no_task_id_is_skipped_with_a_reason() -> None:
    result = harvest([oc("real"), TaskOutcome(task_id="", messages=convo())])
    assert result.skipped_count == 1
    assert result.skips[0].reason == "missing task id"
    # The skipped input reaches none of the datasets, and the good one still does.
    assert result.accepted_count == 1
    assert len(result.rlvr) == 1


# --------------------------------------------------------------------------- #
# The transcript bridge
# --------------------------------------------------------------------------- #


def test_outcome_from_transcript_prefers_recorded_agent_state() -> None:
    messages = convo()
    events = (
        TurnStart(turn_index=0),
        ToolStart(tool_use_id="c1", name="edit", arguments={"path": "app.py"}),
        ToolEnd(tool_use_id="c1", name="edit", result=ToolResult(ok=True, content="ok")),
        TurnEnd(turn_index=0, state=TurnState.DONE, agent_state=AgentState(messages=messages)),
    )
    outcome = outcome_from_transcript(
        ReadResult(events=events),
        task_id="x",
        verify_passed=True,
        category="edit",
        prompt="Fix add() in app.py",
    )
    assert outcome.messages == messages  # the recorded conversation, verbatim
    assert outcome.turns == 1
    assert len(outcome.tool_calls) == 1
    assert outcome.tool_calls[0].ok
    assert outcome.verify_passed


def test_outcome_from_transcript_folds_events_when_no_state_was_recorded() -> None:
    events = (
        TurnStart(turn_index=0),
        TextDelta(text="I'll fix it."),
        ToolStart(tool_use_id="c1", name="edit", arguments={"path": "app.py"}),
        ToolEnd(tool_use_id="c1", name="edit", result=ToolResult(ok=True, content="edited")),
        TurnEnd(turn_index=0, state=TurnState.DONE),
    )
    outcome = outcome_from_transcript(
        ReadResult(events=events), task_id="x", verify_passed=False, prompt="Fix it"
    )
    roles = [message.role for message in outcome.messages]
    assert roles == [Role.USER, Role.ASSISTANT, Role.TOOL]  # prompt prepended, turns folded
    assert outcome.messages[0].text == "Fix it"
    assert outcome.messages[1].tool_uses[0].name == "edit"
    assert outcome.messages[2].content_blocks[0] == ToolResultBlock(
        tool_use_id="c1", content="edited"
    )
    assert outcome.turns == 1


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_harvest_and_write_are_byte_identical_across_runs(tmp_path: Path) -> None:
    first = harvest(ten_outcomes())
    second = harvest(ten_outcomes())
    assert first == second

    a = write_dataset(first, tmp_path / "a")
    b = write_dataset(second, tmp_path / "b")
    for name in ("sft", "dpo", "rlvr"):
        assert a[name].read_bytes() == b[name].read_bytes()


# --------------------------------------------------------------------------- #
# Cross-project contract lock
# --------------------------------------------------------------------------- #
#
# The training project's harvest→trainer bridge
# (``training/src/ronin_training/adapter/from_harvest.py``) inverts these two contracts
# to turn ``sft.jsonl`` into the trainer's ``messages`` rows. It lives in a separate
# project that cannot import this one, so a silent change to the sharegpt tags or the
# tool-call wire format here would break the bridge with no failing test on that side.
# These pin the exact surface the bridge depends on, so the break surfaces *here*.


def test_sharegpt_tags_are_the_ones_the_bridge_inverts() -> None:
    assert FROM_TAG[Role.SYSTEM] == "system"
    assert FROM_TAG[Role.USER] == "human"
    assert FROM_TAG[Role.ASSISTANT] == "gpt"
    assert FROM_TAG[Role.TOOL] == "tool"
    assert ASSISTANT_TAG == "gpt"


def test_tool_call_wire_format_is_the_one_the_bridge_parses() -> None:
    text = render_tool_call(ToolUse(id="c0", name="read", arguments={"path": "app.py"}))
    assert text.startswith("<tool_call>") and text.endswith("</tool_call>")
    body = json.loads(text[len("<tool_call>") : -len("</tool_call>")])
    assert body == {"name": "read", "arguments": {"path": "app.py"}}
