"""SFT extraction: what gets emitted, what never does, and what the cap does."""
from __future__ import annotations

import json

import pytest
from ronin_training.harvest.prompt import render_prefix
from ronin_training.harvest.sft import (
    DEFAULT_MAX_EXAMPLES,
    TARGET_MAX_EXAMPLES,
    TARGET_MIN_EXAMPLES,
    SftConfig,
    extract,
    rebase_cap,
    write_jsonl,
)
from ronin_training.harvest.trajectory import (
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
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    ),
)


def _run(run_id: str, turns: int = 2, *, verified: bool = True, task: str = "t1") -> Trajectory:
    return Trajectory(
        task_id=task,
        run_id=run_id,
        prompt=f"inspect src/{run_id}/module.py",
        model="fixture-model",
        system="You are Ronin.",
        tools=_TOOLS,
        verified=verified,
        steps=tuple(
            Step(
                index=i,
                text=f"step {i}",
                calls=(
                    RecordedCall(
                        id=f"c{i}", name="read_file", arguments={"path": f"src/{run_id}/f{i}.py"}
                    ),
                ),
                results=(RecordedResult(call_id=f"c{i}", name="read_file", ok=True, content="x"),),
            )
            for i in range(turns)
        ),
    )


def test_a_failing_trajectory_never_yields_an_sft_example():
    examples, counts, _ = extract([_run("bad", verified=False)])
    assert examples == ()
    assert counts.emitted == 0
    assert counts.filters.dropped_unverified == 1


def test_an_example_prompt_is_byte_identical_to_the_canonical_renderer_for_that_state():
    """The test that would have caught the 0/4: the row the pipeline writes must be
    the same bytes the renderer produces for the same trajectory state."""
    traj = _run("a", turns=3)
    examples, _counts, _ = extract([traj], config=SftConfig(deduplicate=False))
    for step_index, example in enumerate(examples):
        expected = render_prefix(traj, step_index)
        assert example.payload.digest() == expected.digest()
        assert json.dumps(example.payload.as_row(), sort_keys=True) == json.dumps(
            expected.as_row(), sort_keys=True
        )


def test_one_example_is_emitted_per_assistant_turn():
    examples, counts, _ = extract([_run("a", turns=4)])
    assert len(examples) == 4
    assert counts.turns_seen == 4
    assert [e.provenance.turn_index for e in examples] == [0, 1, 2, 3]


def test_a_turn_with_no_tool_call_is_dropped_by_default_and_counted():
    traj = Trajectory(
        task_id="t1",
        run_id="prose",
        prompt="explain it",
        tools=_TOOLS,
        verified=True,
        steps=(Step(index=0, text="here is my summary"),),
    )
    examples, counts, _ = extract([traj])
    assert examples == ()
    assert counts.dropped_no_tool_call == 1

    kept, counts2, _ = extract([traj], config=SftConfig(require_tool_call=False))
    assert len(kept) == 1
    assert counts2.dropped_no_tool_call == 0


def test_the_cap_is_explicit_configurable_and_counted():
    examples, counts, _ = extract([_run("a", turns=10)], config=SftConfig(max_examples=3))
    assert len(examples) == 3
    assert counts.dropped_over_cap == 7
    assert DEFAULT_MAX_EXAMPLES == TARGET_MAX_EXAMPLES == 8_000
    assert TARGET_MIN_EXAMPLES == 3_000


def test_the_cap_truncates_deterministically_rather_than_sampling():
    a, _c1, _b1 = extract([_run("a", turns=6)], config=SftConfig(max_examples=2))
    b, _c2, _b2 = extract([_run("a", turns=6)], config=SftConfig(max_examples=2))
    assert [e.digest() for e in a] == [e.digest() for e in b]


def test_rebase_cap_changes_only_the_cap():
    base = SftConfig(augment_copies=2, seed=9)
    assert rebase_cap(base, 42) == SftConfig(max_examples=42, augment_copies=2, seed=9)


def test_a_zero_or_negative_cap_is_refused():
    with pytest.raises(ValueError, match="max_examples must be > 0"):
        SftConfig(max_examples=0)


def test_augmented_copies_are_flagged_and_carry_their_seed():
    examples, counts, _ = extract(
        [_run("a", turns=2)], config=SftConfig(augment_copies=2, seed=100)
    )
    augmented = [e for e in examples if e.provenance.augmented]
    assert augmented, "augment_copies=2 should have produced perturbed rows"
    assert counts.emitted_augmented == len(augmented)
    for example in augmented:
        assert example.provenance.augment_seed >= 0
        assert "#aug" in example.provenance.run_id
    for example in (e for e in examples if not e.provenance.augmented):
        assert example.provenance.augment_seed == -1


def test_provenance_names_the_task_run_and_model_on_every_row():
    examples, _counts, _ = extract([_run("a", turns=2)])
    for example in examples:
        record = example.as_record()
        assert record["meta"]["task_id"] == "t1"
        assert record["meta"]["run_id"] == "a"
        assert record["meta"]["model"] == "fixture-model"
        assert record["meta"]["source"] == "eval_suite"


def test_the_trainer_facing_row_carries_only_messages_and_tools():
    examples, _counts, _ = extract([_run("a", turns=1)])
    assert set(examples[0].as_row()) == {"messages", "tools"}
    assert set(examples[0].as_record()) == {"messages", "tools", "meta"}


def test_the_completion_is_the_turn_the_passing_run_actually_took():
    traj = _run("a", turns=2)
    examples, _counts, _ = extract([traj])
    call = examples[1].completion["tool_calls"][0]["function"]
    assert call["name"] == "read_file"
    assert call["arguments"] == {"path": "src/a/f1.py"}


def test_identical_rows_from_different_runs_are_deduplicated():
    """Two runs that differ only in run_id render the same bytes; keeping both would
    weight one behaviour twice for no new signal."""
    a = _run("same", turns=2)
    b = Trajectory(
        task_id=a.task_id,
        run_id="other",
        prompt=a.prompt,
        model=a.model,
        system=a.system,
        tools=a.tools,
        steps=a.steps,
        verified=True,
    )
    examples, counts, _ = extract([a, b])
    assert len(examples) == 2
    assert counts.dropped_duplicate == 2


def test_the_shortfall_against_the_target_band_is_reported_not_padded():
    _examples, counts, _ = extract([_run("a", turns=2)])
    assert counts.emitted == 2
    assert counts.shortfall() == TARGET_MIN_EXAMPLES - 2
    assert "below the 3000-example target" in counts.summary()


def test_writing_jsonl_uses_lf_and_one_row_per_line(tmp_path):
    examples, _counts, _ = extract([_run("a", turns=2)])
    out = tmp_path / "sft.jsonl"
    assert write_jsonl(out, examples) == 2
    raw = out.read_bytes()
    assert b"\r\n" not in raw
    assert raw.count(b"\n") == 2
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    assert all("meta" in row for row in rows)


def test_writing_without_meta_produces_the_bare_trainer_form(tmp_path):
    examples, _counts, _ = extract([_run("a", turns=1)])
    out = tmp_path / "bare.jsonl"
    write_jsonl(out, examples, with_meta=False)
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert set(row) == {"messages", "tools"}


def test_an_empty_corpus_yields_nothing_and_says_so():
    examples, counts, budgets = extract([])
    assert examples == ()
    assert budgets == {}
    assert counts.as_dict()["emitted"] == 0
