"""Augmentation must be internally consistent, or it teaches the model to contradict itself.

The invariant under test: a rename applied to the prompt applies to the assistant
turn, to the tool arguments, to the observations, and to every later reference. The
tests assert it directly rather than trusting the one-table-one-pass implementation.
"""
from __future__ import annotations

import json

from ronin_training.harvest.augment import augment, plan_renaming
from ronin_training.harvest.prompt import render_prefix
from ronin_training.harvest.trajectory import (
    RecordedCall,
    RecordedResult,
    Step,
    ToolSchema,
    Trajectory,
)
from ronin_training.validators import load_registry, validate_example

_TOOLS = (
    ToolSchema(
        name="read_file",
        description="Read a file.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolSchema(
        name="edit_file",
        description="Replace an exact string in a file.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    ),
)


def _run() -> Trajectory:
    return Trajectory(
        task_id="bump-timeout",
        run_id="run-1",
        prompt="In src/gateway/dispatcher.py, raise connect_timeout from 5 to 30.",
        model="fixture-model",
        system="You are Ronin.",
        tools=_TOOLS,
        verified=True,
        steps=(
            Step(
                index=0,
                text="Reading src/gateway/dispatcher.py first.",
                calls=(
                    RecordedCall(
                        id="c0", name="read_file", arguments={"path": "src/gateway/dispatcher.py"}
                    ),
                ),
                results=(
                    RecordedResult(
                        call_id="c0",
                        name="read_file",
                        ok=True,
                        content="connect_timeout = 5\n",
                    ),
                ),
            ),
            Step(
                index=1,
                text="Now editing src/gateway/dispatcher.py.",
                calls=(
                    RecordedCall(
                        id="c1",
                        name="edit_file",
                        arguments={
                            "path": "src/gateway/dispatcher.py",
                            "old_string": "connect_timeout = 5",
                            "new_string": "connect_timeout = 30",
                        },
                    ),
                ),
                results=(
                    RecordedResult(call_id="c1", name="edit_file", ok=True, content="1 replacement"),
                ),
            ),
        ),
    )


def test_a_rename_reaches_the_prompt_the_target_and_every_later_reference():
    original = _run()
    perturbed = augment(original, seed=11)
    table = plan_renaming(original, seed=11).table
    assert table, "the fixture has renameable vocabulary"

    blob = json.dumps(
        {
            "prompt": perturbed.prompt,
            "steps": [
                {
                    "text": s.text,
                    "calls": [dict(c.arguments) for c in s.calls],
                    "results": [r.content for r in s.results],
                }
                for s in perturbed.steps
            ],
        }
    )
    for old, new in table.items():
        assert old not in blob, f"{old!r} survived the rename somewhere in the example"
        assert new in blob, f"{new!r} never appears — the rename did not reach the row"


def test_the_renamed_path_in_the_target_is_the_renamed_path_in_the_prompt():
    """The one contradiction that matters: the model must not be trained to edit a
    file the prompt never mentioned."""
    perturbed = augment(_run(), seed=3)
    edit_path = perturbed.steps[1].calls[0].arguments["path"]
    assert edit_path in perturbed.prompt
    assert edit_path == perturbed.steps[0].calls[0].arguments["path"]


def test_the_rendered_prompt_and_completion_agree_after_augmentation():
    perturbed = augment(_run(), seed=5)
    payload = render_prefix(perturbed, 1)
    rendered = json.dumps(payload.with_completion(
        {"role": "assistant", "content": perturbed.steps[1].text}
    ).as_row())
    assert "dispatcher" not in rendered
    assert perturbed.steps[1].calls[0].arguments["path"] in rendered


def test_augmentation_is_deterministic_in_its_seed():
    a = augment(_run(), seed=42)
    b = augment(_run(), seed=42)
    c = augment(_run(), seed=43)
    assert a.prompt == b.prompt
    assert a.prompt != c.prompt


def test_tool_names_and_argument_keys_are_never_renamed():
    """Renaming ``read_file`` or ``old_string`` would produce a call the registry
    rejects — the worst possible outcome of an augmentation pass."""
    perturbed = augment(_run(), seed=9)
    assert [c.name for _s, c in perturbed.calls()] == ["read_file", "edit_file"]
    assert set(perturbed.steps[1].calls[0].arguments) == {"path", "old_string", "new_string"}


def test_the_toolset_and_system_prompt_are_left_alone():
    """They are the harness's own text, byte-identical at inference. Rewriting them
    would reintroduce the train/inference prompt divergence this pipeline avoids."""
    original = _run()
    perturbed = augment(original, seed=13)
    assert perturbed.tools == original.tools
    assert perturbed.system == original.system


def test_the_run_id_records_that_the_row_is_a_perturbed_copy():
    perturbed = augment(_run(), seed=2)
    assert perturbed.run_id.startswith("run-1#")
    assert perturbed.task_id == "bump-timeout"
    assert perturbed.model == "fixture-model"


def test_an_augmented_row_still_validates_against_the_real_tool_registry():
    """A perturbed example must remain a legal Ronin call, not merely a legal string."""
    perturbed = augment(_run(), seed=17)
    payload = render_prefix(perturbed, 2)
    row = {
        "id": "harvest_augment_case",
        "family": "harvest",
        "source_volume": "harvest-test",
        "license": "project-owned",
        "messages": payload.as_row()["messages"],
    }
    assert validate_example(row) == []
    assert {"read_file", "edit_file"} <= set(load_registry())


def test_a_trajectory_with_nothing_renameable_survives_unchanged():
    bare = Trajectory(
        task_id="t",
        run_id="r",
        prompt="say hello",
        steps=(Step(index=0, text="hello"),),
        verified=True,
    )
    assert plan_renaming(bare, seed=1).is_empty()
    assert augment(bare, seed=1).prompt == "say hello"


def test_structural_names_are_preserved_because_they_are_conventions():
    """``src`` and ``tests`` are what the example is *about*; the fixture's own
    directory and module names are what it must not memorise."""
    perturbed = augment(_run(), seed=21)
    path = perturbed.steps[0].calls[0].arguments["path"]
    assert path.startswith("src/")
    assert path.endswith(".py")
    assert "gateway" not in path
