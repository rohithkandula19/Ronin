"""Preference pairs: schema validity, and where the preferred side came from.

The integrity property under test is that ``chosen`` never lies about its origin. A
donor-backed pair names the passing run it came from; a synthesized one is flagged and
names no donor; and a negative with no honest correction produces no row at all.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import pytest
from ronin_training.harvest.dpo import (
    SCHEMA_PATH,
    ChosenTurn,
    build_pairs,
    find_donor,
    load_schema,
    synthesize,
    to_text_row,
    validate_row,
    write_jsonl,
)
from ronin_training.harvest.negatives import (
    DEFAULT_TOOL_ROLES,
    NegativeClass,
    mine,
)
from ronin_training.harvest.trajectory import (
    DENIAL_PREFIX,
    RecordedCall,
    RecordedResult,
    Step,
    ToolSchema,
    Trajectory,
)

_TOOLS = (
    ToolSchema(
        name="read_file",
        description="Read a file.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["path"],
        },
    ),
    ToolSchema(
        name="write_file",
        description="Write a file.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    ),
)
_DENIAL = "leave the vendored tree alone"


def _blind_write(task: str = "t1") -> Trajectory:
    return Trajectory(
        task_id=task,
        run_id="run-blind",
        prompt="set VERSION in src/app/meta.py",
        model="fixture-model",
        system="You are Ronin.",
        tools=_TOOLS,
        verified=False,
        steps=(
            Step(
                index=0,
                text="Writing it.",
                calls=(
                    RecordedCall(
                        id="c0",
                        name="write_file",
                        arguments={"path": "src/app/meta.py", "content": "VERSION = 2\n"},
                    ),
                ),
                results=(
                    RecordedResult(
                        call_id="c0",
                        name="write_file",
                        ok=False,
                        error="src/app/meta.py has not been read in this session",
                    ),
                ),
            ),
        ),
    )


def _careful_donor(task: str = "t1") -> Trajectory:
    return Trajectory(
        task_id=task,
        run_id="run-good",
        prompt="set VERSION in src/app/meta.py",
        model="fixture-model",
        system="You are Ronin.",
        tools=_TOOLS,
        verified=True,
        steps=(
            Step(
                index=0,
                text="Reading first.",
                calls=(
                    RecordedCall(id="g0", name="read_file", arguments={"path": "src/app/meta.py"}),
                ),
                results=(
                    RecordedResult(
                        call_id="g0", name="read_file", ok=True, content="VERSION = 1\n"
                    ),
                ),
            ),
        ),
    )


def _denial_ignored() -> Trajectory:
    args = {"path": "vendor/x.py", "content": "1\n"}
    return Trajectory(
        task_id="t1",
        run_id="run-denied",
        prompt="patch vendor/x.py",
        model="fixture-model",
        tools=_TOOLS,
        verified=False,
        steps=(
            Step(
                index=0,
                calls=(RecordedCall(id="d0", name="write_file", arguments=dict(args)),),
                results=(
                    RecordedResult(
                        call_id="d0",
                        name="write_file",
                        ok=False,
                        error=f"{DENIAL_PREFIX}{_DENIAL}",
                    ),
                ),
            ),
            Step(
                index=1,
                text="Trying again.",
                calls=(RecordedCall(id="d1", name="write_file", arguments=dict(args)),),
                results=(
                    RecordedResult(
                        call_id="d1",
                        name="write_file",
                        ok=False,
                        error=f"{DENIAL_PREFIX}{_DENIAL}",
                    ),
                ),
            ),
        ),
    )


def _missing_required_key() -> Trajectory:
    return Trajectory(
        task_id="lonely-task",
        run_id="run-missing",
        prompt="write src/app/meta.py",
        tools=_TOOLS,
        verified=False,
        steps=(
            Step(
                index=0,
                calls=(RecordedCall(id="m0", name="write_file", arguments={"path": "a.py"}),),
                results=(
                    RecordedResult(
                        call_id="m0", name="write_file", ok=False, error="content is required"
                    ),
                ),
            ),
        ),
    )


def _wrong_type() -> Trajectory:
    return Trajectory(
        task_id="lonely-task",
        run_id="run-typed",
        prompt="read src/app/meta.py",
        tools=_TOOLS,
        verified=False,
        steps=(
            Step(
                index=0,
                calls=(
                    RecordedCall(
                        id="w0", name="read_file", arguments={"path": "a.py", "limit": "40"}
                    ),
                ),
                results=(
                    RecordedResult(
                        call_id="w0", name="read_file", ok=False, error="limit must be an integer"
                    ),
                ),
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# Origin of the preferred side
# --------------------------------------------------------------------------- #


def test_a_real_corrected_turn_is_preferred_when_one_exists():
    negatives = [n for n in mine(_blind_write()) if n.kind is NegativeClass.EDIT_WITHOUT_READ]
    pairs, counts = build_pairs(
        negatives, donors=[_careful_donor()], roles=DEFAULT_TOOL_ROLES
    )
    assert len(pairs) == 1
    assert pairs[0].chosen.synthesized is False
    assert pairs[0].chosen.donor_run_id == "run-good"
    assert pairs[0].chosen.donor_turn_index == 0
    assert counts.from_donor["edit_without_read"] == 1
    assert counts.synthesized["edit_without_read"] == 0


def test_a_synthesized_corrected_side_is_flagged_in_the_record():
    negatives = [n for n in mine(_blind_write()) if n.kind is NegativeClass.EDIT_WITHOUT_READ]
    pairs, counts = build_pairs(negatives, donors=[], roles=DEFAULT_TOOL_ROLES)
    assert len(pairs) == 1
    assert pairs[0].chosen.synthesized is True
    assert pairs[0].chosen.donor_run_id == ""
    row = pairs[0].as_row()
    assert row["meta"]["synthesized_chosen"] is True
    assert "donor_run_id" not in row["meta"]
    assert counts.synthesized["edit_without_read"] == 1


def test_the_synthesized_correction_for_a_blind_edit_is_the_missing_read():
    """The brief's own example: the corrected turn is the same edit preceded by the read."""
    negative = next(
        n for n in mine(_blind_write()) if n.kind is NegativeClass.EDIT_WITHOUT_READ
    )
    chosen = synthesize(negative, roles=DEFAULT_TOOL_ROLES)
    assert chosen is not None
    call = chosen.message["tool_calls"][0]["function"]
    assert call["name"] == "read_file"
    assert call["arguments"] == {"path": "src/app/meta.py"}


def test_the_synthesized_correction_for_a_denial_quotes_the_gates_own_reason():
    negative = next(
        n for n in mine(_denial_ignored()) if n.kind is NegativeClass.IGNORED_DENIAL
    )
    chosen = synthesize(negative, roles=DEFAULT_TOOL_ROLES)
    assert chosen is not None
    assert _DENIAL in chosen.message["content"]
    assert "tool_calls" not in chosen.message


def test_a_negative_with_no_honest_correction_yields_no_pair_and_is_counted():
    """A missing required key cannot be repaired without inventing the value."""
    negatives = [
        n
        for n in mine(_missing_required_key())
        if n.kind is NegativeClass.MALFORMED_TOOL_JSON
    ]
    assert negatives
    pairs, counts = build_pairs(negatives, donors=[], roles=DEFAULT_TOOL_ROLES)
    assert pairs == ()
    assert counts.unconstructible["malformed_tool_json"] == len(negatives)
    assert counts.total() == 0


def test_a_losslessly_coercible_wrong_type_is_repaired_from_the_schema():
    negative = next(
        n
        for n in mine(_wrong_type())
        if n.kind is NegativeClass.MALFORMED_TOOL_JSON and n.evidence.get("defect") == "schema"
    )
    chosen = synthesize(negative, roles=DEFAULT_TOOL_ROLES)
    assert chosen is not None
    assert chosen.message["tool_calls"][0]["function"]["arguments"] == {
        "path": "a.py",
        "limit": 40,
    }


def test_a_donor_from_a_different_task_is_never_used():
    negatives = [
        n for n in mine(_blind_write("task-a")) if n.kind is NegativeClass.EDIT_WITHOUT_READ
    ]
    assert find_donor(negatives[0], [_careful_donor("task-b")], roles=DEFAULT_TOOL_ROLES) is None


def test_a_failing_trajectory_is_never_used_as_a_donor():
    donor = _careful_donor()
    unverified = Trajectory(
        task_id=donor.task_id,
        run_id=donor.run_id,
        prompt=donor.prompt,
        tools=donor.tools,
        steps=donor.steps,
        verified=False,
    )
    negatives = [n for n in mine(_blind_write()) if n.kind is NegativeClass.EDIT_WITHOUT_READ]
    assert find_donor(negatives[0], [unverified], roles=DEFAULT_TOOL_ROLES) is None


def test_donor_and_synthesized_pairs_are_never_silently_mixed():
    with pytest.raises(ValueError, match="cannot also name a donor"):
        ChosenTurn(
            message={"role": "assistant", "content": "x"}, synthesized=True, donor_run_id="r"
        )
    with pytest.raises(ValueError, match="must name the passing run"):
        ChosenTurn(message={"role": "assistant", "content": "x"}, synthesized=False)


def test_synthesis_can_be_turned_off_to_measure_how_much_is_real():
    negatives = mine(_blind_write())
    _pairs, with_synth = build_pairs(negatives, donors=[], roles=DEFAULT_TOOL_ROLES)
    _pairs2, donor_only = build_pairs(
        negatives, donors=[], roles=DEFAULT_TOOL_ROLES, allow_synthesized=False
    )
    assert with_synth.total() > 0
    assert donor_only.total() == 0
    assert sum(donor_only.unconstructible.values()) == sum(with_synth.mined.values())


# --------------------------------------------------------------------------- #
# The row format
# --------------------------------------------------------------------------- #


def test_every_emitted_row_validates_against_the_schema():
    corpus = [_blind_write(), _denial_ignored(), _wrong_type(), _missing_required_key()]
    negatives = [n for traj in corpus for n in mine(traj)]
    pairs, _counts = build_pairs(
        negatives, donors=[_careful_donor()], roles=DEFAULT_TOOL_ROLES
    )
    assert pairs
    for pair in pairs:
        assert validate_row(pair.as_row()) == [], pair.as_row()


def test_the_row_carries_the_three_keys_a_dpo_trainer_reads():
    negatives = mine(_blind_write())
    pairs, _counts = build_pairs(negatives, donors=[], roles=DEFAULT_TOOL_ROLES)
    row = pairs[0].as_row()
    assert {"prompt", "chosen", "rejected"} <= set(row)
    assert isinstance(row["prompt"], list)
    assert row["chosen"][0]["role"] == "assistant"
    assert row["rejected"][0]["role"] == "assistant"


def test_the_rejected_side_is_the_turn_the_model_actually_took():
    negatives = [n for n in mine(_blind_write()) if n.kind is NegativeClass.EDIT_WITHOUT_READ]
    pairs, _counts = build_pairs(negatives, donors=[], roles=DEFAULT_TOOL_ROLES)
    rejected = pairs[0].as_row()["rejected"][0]
    assert rejected["tool_calls"][0]["function"]["name"] == "write_file"


def test_the_row_records_the_rule_the_chosen_side_is_correcting():
    negatives = [n for n in mine(_blind_write()) if n.kind is NegativeClass.EDIT_WITHOUT_READ]
    pairs, _counts = build_pairs(negatives, donors=[], roles=DEFAULT_TOOL_ROLES)
    assert "before writing it" in pairs[0].as_row()["meta"]["rule"]


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        (lambda row: row.pop("meta"), "meta"),
        (lambda row: row.pop("chosen"), "chosen"),
        (lambda row: row.update(surprise=1), "surprise"),
        (lambda row: row["meta"].update(negative_class="not_a_class"), "not_a_class"),
        (
            lambda row: row["rejected"][0]["tool_calls"][0]["function"].update(
                arguments='{"path": "a.py"}'
            ),
            "arguments",
        ),
    ],
)
def test_the_schema_rejects_a_malformed_row(mutate, expect):
    negatives = [n for n in mine(_blind_write()) if n.kind is NegativeClass.EDIT_WITHOUT_READ]
    pairs, _counts = build_pairs(negatives, donors=[], roles=DEFAULT_TOOL_ROLES)
    row = pairs[0].as_row()
    mutate(row)
    problems = validate_row(row)
    assert problems
    assert any(expect in problem for problem in problems)


def test_the_schema_rejects_a_row_claiming_both_origins_for_its_chosen_side():
    negatives = [n for n in mine(_blind_write()) if n.kind is NegativeClass.EDIT_WITHOUT_READ]
    pairs, _counts = build_pairs(negatives, donors=[], roles=DEFAULT_TOOL_ROLES)
    row = pairs[0].as_row()
    row["meta"]["donor_run_id"] = "run-good"
    assert any("both origins" in problem for problem in validate_row(row))


def test_the_schema_file_ships_inside_the_package():
    assert SCHEMA_PATH.is_file()
    assert SCHEMA_PATH.parent.name == "harvest"
    assert load_schema()["required"] == ["prompt", "chosen", "rejected", "meta"]


def test_writing_refuses_the_whole_file_when_any_row_is_invalid(tmp_path):
    class _Broken:
        def as_row(self) -> dict[str, object]:
            return {"prompt": [], "chosen": [], "rejected": [], "meta": {}}

    out = tmp_path / "dpo.jsonl"
    with pytest.raises(ValueError, match="refusing to write an invalid DPO dataset"):
        write_jsonl(out, [_Broken()])  # type: ignore[list-item]
    assert not out.exists()


def test_writing_valid_rows_uses_lf_and_one_row_per_line(tmp_path):
    negatives = mine(_blind_write())
    pairs, _counts = build_pairs(negatives, donors=[], roles=DEFAULT_TOOL_ROLES)
    out = tmp_path / "dpo.jsonl"
    written = write_jsonl(out, pairs)
    raw = out.read_bytes()
    assert b"\r\n" not in raw
    assert raw.count(b"\n") == written
    assert all(validate_row(json.loads(line)) == [] for line in raw.decode().splitlines())


def test_the_text_form_uses_an_injected_template_and_the_canonical_wire_format():
    """No chat template is implemented in this package; the caller injects the
    tokenizer's own. The completions still come from ``ronin_dialect``."""
    calls: list[int] = []

    def fake_template(
        messages: Sequence[Mapping[str, object]], tools: Sequence[Mapping[str, object]]
    ) -> str:
        calls.append(len(messages))
        return f"<rendered {len(messages)} messages, {len(tools)} tools>"

    negatives = [n for n in mine(_blind_write()) if n.kind is NegativeClass.EDIT_WITHOUT_READ]
    pairs, _counts = build_pairs(negatives, donors=[], roles=DEFAULT_TOOL_ROLES)
    row = to_text_row(pairs[0], fake_template)
    assert calls == [2]
    assert row["prompt"] == "<rendered 2 messages, 2 tools>"
    assert row["chosen"].startswith("<tool_call>")
    assert '"name": "read_file"' in row["chosen"]
    assert '"name": "write_file"' in row["rejected"]


def test_per_class_counts_are_reported_including_zeros():
    pairs, counts = build_pairs(mine(_blind_write()), donors=[], roles=DEFAULT_TOOL_ROLES)
    assert set(counts.mined) == {str(k) for k in NegativeClass}
    assert counts.mined["looping"] == 0
    assert counts.total() == len(pairs)
    assert "unconstructible" in counts.summary()
