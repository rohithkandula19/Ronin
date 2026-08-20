"""The holdout split. The property that matters is disjointness, so it is tested as one.

A single hand-picked example would not catch the bug this module exists to prevent —
leakage shows up on *some* shapes of corpus (uneven task sizes, task counts that round
badly, one enormous task) and not others. So the disjointness test sweeps a generated
space of task/example shapes, seeds and fractions, and asserts the invariant on every
one of them.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from ronin_training.adapter.dataset import (
    TASK_ID_KEYS,
    DatasetError,
    Example,
    TaskSplit,
    examples_from_rows,
    read_examples,
    read_jsonl,
    split_by_task,
    task_id_of,
    verify_no_task_leak,
    write_split,
)


def _corpus(*, tasks: int, per_task: int, skew: int = 0) -> list[Example]:
    """A corpus of ``tasks`` tasks. ``skew`` extra examples go to the first task."""
    out: list[Example] = []
    for task_index in range(tasks):
        count = per_task + (skew if task_index == 0 else 0)
        for example_index in range(count):
            task = f"task-{task_index:03d}"
            out.append(
                Example(
                    task_id=task,
                    example_id=f"{task}-ex-{example_index:03d}",
                    row={"task_id": task, "messages": [], "n": example_index},
                )
            )
    return out


# ------------------------------------------------------- the property that matters


@pytest.mark.parametrize("tasks", [2, 3, 5, 11, 20, 47])
@pytest.mark.parametrize("per_task", [1, 2, 7])
@pytest.mark.parametrize("skew", [0, 13])
@pytest.mark.parametrize("seed", [0, 1, 99])
@pytest.mark.parametrize("fraction", [0.05, 0.10, 0.25])
def test_no_task_id_ever_appears_on_both_sides_of_the_split(
    tasks: int, per_task: int, skew: int, seed: int, fraction: float
) -> None:
    """The invariant, over a generated space of corpus shapes, seeds and fractions."""
    examples = _corpus(tasks=tasks, per_task=per_task, skew=skew)
    split = split_by_task(examples, fraction=fraction, seed=seed)

    train_ids = {example.task_id for example in split.train}
    holdout_ids = {example.task_id for example in split.holdout}
    assert not train_ids & holdout_ids, sorted(train_ids & holdout_ids)
    assert split.leaked_tasks == ()
    assert not set(split.train_tasks) & set(split.holdout_tasks)

    # Nothing is lost, and nothing is duplicated.
    assert len(split.train) + len(split.holdout) == len(examples)
    assert train_ids | holdout_ids == {example.task_id for example in examples}
    all_ids = [example.example_id for example in (*split.train, *split.holdout)]
    assert len(all_ids) == len(set(all_ids))

    # Never degenerate: both sides always hold at least one task.
    assert split.train_tasks and split.holdout_tasks


@pytest.mark.parametrize("seed", [0, 7])
def test_the_split_is_reproducible_across_calls_and_process_independent(seed: int) -> None:
    """Ordering is a sha256 of the task id, not ``hash()`` — which is salted per run."""
    examples = _corpus(tasks=17, per_task=3)
    first = split_by_task(examples, seed=seed)
    second = split_by_task(list(reversed(examples)), seed=seed)
    assert first.holdout_tasks == second.holdout_tasks
    assert [e.example_id for e in first.holdout] == [e.example_id for e in second.holdout]


def test_a_different_seed_generally_selects_different_tasks() -> None:
    examples = _corpus(tasks=40, per_task=2)
    picks = {split_by_task(examples, seed=seed).holdout_tasks for seed in range(6)}
    assert len(picks) > 1, "every seed produced the same holdout — ordering is not seeded"


def test_holding_out_ten_percent_of_tasks_is_not_ten_percent_of_examples() -> None:
    """Reported explicitly, because someone will read 0.10 as a statement about rows."""
    examples = _corpus(tasks=10, per_task=2, skew=80)  # task-000 has 82 of 100 rows
    split = split_by_task(examples, fraction=0.10, seed=0)
    assert len(split.holdout_tasks) == 1
    assert split.example_fraction_held_out != pytest.approx(0.10)
    assert 0.0 < split.example_fraction_held_out < 1.0


def test_a_single_task_corpus_refuses_to_split() -> None:
    with pytest.raises(DatasetError, match="more task variety"):
        split_by_task(_corpus(tasks=1, per_task=20))


def test_an_empty_corpus_refuses_to_split() -> None:
    with pytest.raises(DatasetError, match="empty dataset"):
        split_by_task([])


@pytest.mark.parametrize("fraction", [0.0, 1.0, -0.1, 1.5])
def test_a_nonsense_fraction_is_rejected(fraction: float) -> None:
    with pytest.raises(DatasetError, match="fraction must be in"):
        split_by_task(_corpus(tasks=4, per_task=2), fraction=fraction)


def test_a_huge_fraction_still_leaves_a_training_task() -> None:
    split = split_by_task(_corpus(tasks=3, per_task=2), fraction=0.49)
    assert split.train_tasks and split.holdout_tasks


def test_verify_no_task_leak_catches_a_hand_built_leaky_split() -> None:
    """The check is independent of the splitter, so it catches a leak from anywhere."""
    shared = Example(task_id="t", example_id="a", row={"task_id": "t", "messages": []})
    other = Example(task_id="t", example_id="b", row={"task_id": "t", "messages": []})
    leaky = TaskSplit(
        train=(shared,), holdout=(other,), train_tasks=("t",), holdout_tasks=("t",)
    )
    assert leaky.leaked_tasks == ("t",)
    with pytest.raises(DatasetError, match="task leakage"):
        verify_no_task_leak(leaky)


def test_a_split_whose_bookkeeping_disagrees_with_its_rows_is_rejected() -> None:
    example = Example(task_id="t", example_id="a", row={"task_id": "t", "messages": []})
    lying = TaskSplit(
        train=(example,), holdout=(), train_tasks=("other",), holdout_tasks=()
    )
    with pytest.raises(DatasetError, match="does not declare"):
        verify_no_task_leak(lying)


# ------------------------------------------------------------- the input contract


@pytest.mark.parametrize("key", [k for k in TASK_ID_KEYS if "." not in k])
def test_any_of_the_accepted_flat_task_keys_is_read(key: str) -> None:
    """The harvest pipeline picks one of these; this half must not care which."""
    assert task_id_of({key: "recover-after-denial", "messages": []}) == "recover-after-denial"


def test_the_task_id_is_read_out_of_the_harvest_pipelines_meta_block() -> None:
    """The shape ``ronin_training.harvest`` actually writes: task id nested in ``meta``.

    A flat-key-only lookup would reject every real harvested row, so this is the one
    reconciliation between the two halves of the phase that has to be asserted.
    """
    row = {
        "messages": [{"role": "user", "content": "go"}],
        "tools": [],
        "meta": {
            "task_id": "gate-denial-recovery",
            "run_id": "r1",
            "turn_index": 0,
        },
    }
    assert task_id_of(row) == "gate-denial-recovery"
    examples = examples_from_rows([row], kind="sft")
    assert examples[0].task_id == "gate-denial-recovery"


def test_a_preference_row_in_the_harvest_pipelines_shape_is_read() -> None:
    row = {
        "prompt": [{"role": "user", "content": "go"}],
        "chosen": [{"role": "assistant", "content": "ok"}],
        "rejected": [{"role": "assistant", "content": "no"}],
        "meta": {"task_id": "malformed-json", "run_id": "r1"},
    }
    assert examples_from_rows([row], kind="dpo")[0].task_id == "malformed-json"


def test_a_flat_task_id_still_wins_over_a_missing_meta_block() -> None:
    assert task_id_of({"task_id": "flat", "messages": []}) == "flat"
    assert task_id_of({"meta": {}, "task_id": "flat", "messages": []}) == "flat"


def test_a_non_mapping_meta_does_not_crash_the_lookup() -> None:
    with pytest.raises(DatasetError, match="no task identity"):
        task_id_of({"meta": "not a mapping", "messages": []})


def test_a_row_with_no_task_identity_is_rejected_naming_the_keys() -> None:
    with pytest.raises(DatasetError) as caught:
        task_id_of({"messages": []})
    message = str(caught.value)
    for key in TASK_ID_KEYS:
        assert key in message
    assert "defeat the split" in message


def test_a_row_missing_its_required_fields_is_rejected_by_index() -> None:
    with pytest.raises(DatasetError, match=r"row 1 is not a usable sft row"):
        examples_from_rows(
            [{"task_id": "a", "messages": []}, {"task_id": "b"}], kind="sft"
        )
    with pytest.raises(DatasetError, match=r"row 0 is not a usable dpo row"):
        examples_from_rows([{"task_id": "a", "prompt": "p"}], kind="dpo")


def test_a_preference_row_needs_prompt_chosen_and_rejected() -> None:
    examples = examples_from_rows(
        [{"task_id": "a", "prompt": "p", "chosen": "c", "rejected": "r"}], kind="dpo"
    )
    assert examples[0].task_id == "a"


def test_an_unknown_kind_raises_rather_than_using_the_laxer_check() -> None:
    with pytest.raises(DatasetError, match="kind must be"):
        examples_from_rows([], kind="pretrain")


def test_a_malformed_jsonl_line_is_fatal_and_names_its_line_number(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text('{"task_id": "a", "messages": []}\nnot json\n', encoding="utf-8")
    with pytest.raises(DatasetError, match=r"rows.jsonl:2 is not valid JSON"):
        read_jsonl(path)


def test_reading_a_real_jsonl_file_end_to_end(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    rows = [
        {"task_id": "read-then-edit", "messages": [{"role": "user", "content": "hi"}]},
        {"task_id": "gate-denial", "messages": []},
        {"task_id": "gate-denial", "messages": []},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    examples = read_examples(path)
    assert [example.task_id for example in examples] == [
        "read-then-edit",
        "gate-denial",
        "gate-denial",
    ]
    split = split_by_task(examples, fraction=0.5, seed=0)
    assert split.leaked_tasks == ()


# ------------------------------------------------------------------- writing it out


def test_write_split_emits_lf_endings_and_an_auditable_manifest(tmp_path: Path) -> None:
    split = split_by_task(_corpus(tasks=10, per_task=3), fraction=0.10, seed=0)
    written = write_split(split, tmp_path / "out")
    assert sorted(written) == ["holdout", "manifest", "train"]
    for name in ("train", "holdout"):
        raw = written[name].read_bytes()
        assert b"\r\n" not in raw, f"{name}.jsonl has CRLF"
        assert raw.endswith(b"\n")
    manifest = json.loads(written["manifest"].read_text(encoding="utf-8"))
    assert manifest["leaked_tasks"] == []
    assert manifest["train_examples"] + manifest["holdout_examples"] == 30
    assert set(manifest["train_tasks"]) & set(manifest["holdout_tasks"]) == set()
    assert manifest["task_fraction_held_out"] == pytest.approx(0.1)


def test_write_split_refuses_to_write_a_leaky_split(tmp_path: Path) -> None:
    example = Example(task_id="t", example_id="a", row={"task_id": "t", "messages": []})
    leaky = TaskSplit(
        train=(example,), holdout=(example,), train_tasks=("t",), holdout_tasks=("t",)
    )
    with pytest.raises(DatasetError, match="task leakage"):
        write_split(leaky, tmp_path / "out")
