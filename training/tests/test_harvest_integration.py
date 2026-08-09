"""The whole pipeline on real recorded transcripts, with nothing faked but the model.

An eval sweep of one task: two clean passing runs, one passing flail, one failing run
that commits all four mistakes. Everything from the transcript files forward is the
real code — the persistence reader, the renderer, the gates, the augmenter, the miner,
the pair builder and the schema validator.
"""
from __future__ import annotations

import json

from ronin_training.harvest import (
    DEFAULT_TOOL_ROLES,
    NegativeClass,
    SftConfig,
    build_pairs,
    class_counts,
    extract,
    mine_all,
    validate_row,
    write_examples_jsonl,
    write_pairs_jsonl,
)
from ronin_training.harvest.trajectory import DENIAL_PREFIX
from test_harvest_helpers import load_recorded, registry_tools, turn

_TOOLS = registry_tools("read_file", "write_file", "edit_file")
_TASK = "bump-version"
_PROMPT = "set VERSION to 2 in src/app/meta.py"
_DENIAL = "leave the vendored tree alone"


def _clean_events(suffix: str) -> list[object]:
    return [
        *turn(
            index=0,
            text="Reading it first.",
            calls=[("c0", "read_file", {"path": f"src/{suffix}/meta.py"})],
            results=[("c0", "read_file", True, "VERSION = 1\n", "")],
        ),
        *turn(
            index=1,
            text="Applying the change.",
            calls=[
                (
                    "c1",
                    "edit_file",
                    {
                        "path": f"src/{suffix}/meta.py",
                        "old_string": "VERSION = 1",
                        "new_string": "VERSION = 2",
                    },
                )
            ],
            results=[("c1", "edit_file", True, "1 replacement", "")],
        ),
    ]


def _flail_events() -> list[object]:
    events: list[object] = []
    for i in range(9):
        events.extend(
            turn(
                index=i,
                calls=[(f"f{i}", "read_file", {"path": f"src/app/probe{i}.py"})],
                results=[(f"f{i}", "read_file", True, "# nothing here\n", "")],
            )
        )
    return events


def _bad_events() -> list[object]:
    denied = {"path": "vendor/meta.py", "content": "VERSION = 2\n"}
    events: list[object] = []
    for i in range(3):
        events.extend(
            turn(
                index=i,
                text="Writing it." if i == 0 else "Trying that again.",
                calls=[(f"b{i}", "write_file", dict(denied))],
                results=[(f"b{i}", "write_file", False, "", f"{DENIAL_PREFIX}{_DENIAL}")],
                gated=[f"b{i}"],
            )
        )
    events.extend(
        turn(
            index=3,
            text=(
                "Let me re-read.\n<tool_call>\n"
                '{"name": "read_file", "arguments": {path: }}\n'
                "</tool_call>"
            ),
            calls=[("b3", "edit_file", {"path": "src/app/meta.py", "old_string": "1"})],
            results=[("b3", "edit_file", False, "", "new_string is required")],
        )
    )
    return events


def _sweep(tmp_path):
    good_a = load_recorded(
        tmp_path, "run-a", _clean_events("app"), task_id=_TASK, prompt=_PROMPT,
        tools=_TOOLS, verified=True,
    )
    good_b = load_recorded(
        tmp_path, "run-b", _clean_events("core"), task_id=_TASK,
        prompt="set VERSION to 2 in src/core/meta.py", tools=_TOOLS, verified=True,
    )
    flail = load_recorded(
        tmp_path, "run-flail", _flail_events(), task_id=_TASK, prompt=_PROMPT,
        tools=_TOOLS, verified=True,
    )
    bad = load_recorded(
        tmp_path, "run-bad", _bad_events(), task_id=_TASK, prompt=_PROMPT,
        tools=_TOOLS, verified=False,
    )
    return [good_a, good_b, flail, bad]


def test_the_sweep_yields_sft_rows_only_from_the_clean_passing_runs(tmp_path):
    corpus = _sweep(tmp_path)
    examples, counts, budgets = extract(corpus)

    assert budgets[_TASK].median_turns == 2.0
    assert budgets[_TASK].budget == 3.0
    assert counts.filters.as_dict() == {
        "considered": 4,
        "dropped_unverified": 1,
        "dropped_no_steps": 0,
        "dropped_over_turn_budget": 1,
        "kept": 2,
    }
    assert counts.emitted == 4
    assert {e.provenance.run_id for e in examples} == {"run-a", "run-b"}


def test_the_failing_run_contributes_only_negatives_never_an_sft_row(tmp_path):
    corpus = _sweep(tmp_path)
    examples, _counts, _budgets = extract(corpus)
    assert "run-bad" not in {e.provenance.run_id for e in examples}

    negatives = mine_all([t for t in corpus if t.run_id == "run-bad"])
    counts = class_counts(negatives)
    assert all(counts[str(kind)] > 0 for kind in NegativeClass), counts


def test_the_whole_pipeline_writes_two_validated_files(tmp_path):
    corpus = _sweep(tmp_path)
    examples, sft_counts, _budgets = extract(
        corpus, config=SftConfig(augment_copies=1, seed=3)
    )
    negatives = mine_all(corpus, roles=DEFAULT_TOOL_ROLES)
    donors = [t for t in corpus if t.verified]
    pairs, pair_counts = build_pairs(negatives, donors=donors, roles=DEFAULT_TOOL_ROLES)

    out = tmp_path / "out"
    sft_written = write_examples_jsonl(out / "sft.jsonl", examples)
    dpo_written = write_pairs_jsonl(out / "dpo.jsonl", pairs)

    assert sft_written == sft_counts.emitted
    assert dpo_written == pair_counts.total()
    assert dpo_written > 0

    for line in (out / "dpo.jsonl").read_bytes().decode("utf-8").splitlines():
        assert validate_row(json.loads(line)) == []

    for line in (out / "sft.jsonl").read_bytes().decode("utf-8").splitlines():
        row = json.loads(line)
        assert set(row) == {"messages", "tools"} | {"meta"}
        assert row["messages"][-1]["role"] == "assistant"
        # Every row is trained under the run's whole toolset, not just the tools it used.
        assert [t["function"]["name"] for t in row["tools"]] == [
            "read_file", "write_file", "edit_file"
        ]


def test_augmented_rows_and_originals_are_distinguishable_in_the_written_file(tmp_path):
    corpus = _sweep(tmp_path)
    examples, _counts, _budgets = extract(corpus, config=SftConfig(augment_copies=1, seed=5))
    out = tmp_path / "sft.jsonl"
    write_examples_jsonl(out, examples)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    augmented = [r for r in rows if r["meta"]["augmented"]]
    original = [r for r in rows if not r["meta"]["augmented"]]
    assert augmented and original
    assert all(r["meta"]["augment_seed"] >= 0 for r in augmented)
    assert all(r["meta"]["augment_seed"] == -1 for r in original)


def test_every_pair_says_where_its_preferred_side_came_from(tmp_path):
    corpus = _sweep(tmp_path)
    negatives = mine_all(corpus, roles=DEFAULT_TOOL_ROLES)
    pairs, counts = build_pairs(
        negatives, donors=[t for t in corpus if t.verified], roles=DEFAULT_TOOL_ROLES
    )
    for pair in pairs:
        meta = pair.as_row()["meta"]
        if meta["synthesized_chosen"]:
            assert "donor_run_id" not in meta
        else:
            assert meta["donor_run_id"] in {"run-a", "run-b", "run-flail"}
    assert counts.total() == len(pairs)
    assert sum(counts.mined.values()) == len(negatives)


def test_a_sweep_with_no_passing_run_produces_no_sft_corpus_and_says_so(tmp_path):
    bad = load_recorded(
        tmp_path, "run-only-bad", _bad_events(), task_id=_TASK, prompt=_PROMPT,
        tools=_TOOLS, verified=False,
    )
    examples, counts, budgets = extract([bad])
    assert examples == ()
    assert budgets == {}
    assert counts.emitted == 0
    assert counts.shortfall() == 3_000
    # The failures are still usable: that is the point of mining them separately.
    assert mine_all([bad])


async def test_the_demo_runs_offline_and_reports_success(capsys):
    """The demo is how a human sees this subsystem work, so it must not rot silently."""
    from ronin_training.harvest.demo import main

    assert await main() == 0
    printed = capsys.readouterr().out
    assert "rows failing the schema: 0" in printed
    for name in ("malformed_tool_json", "ignored_denial", "looping", "edit_without_read"):
        assert name in printed
