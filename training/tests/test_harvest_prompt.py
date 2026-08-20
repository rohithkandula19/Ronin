"""The renderer tests. The first two are the ones that would have caught 0/4.

`valid_tool_json` scored 0/4 because nothing checked that the prompt a training row
renders is the prompt inference sends, and because three implementations of the
``<tool_call>`` dialect merely happened to agree
(``training/reports/valid_tool_json_root_cause.md``). These tests make both facts
mechanical: the tools block is compared byte-for-byte against the canonical builder
the eval uses, and every rendered target is fed back through the runtime's own parser.
"""
from __future__ import annotations

import json

import pytest
import ronin_dialect
from ronin_training.eval_runner import ronin_tools_for_template
from ronin_training.harvest.prompt import (
    PromptPayload,
    assistant_message,
    assistant_target_text,
    parse_target_text,
    render_prefix,
    tool_messages,
    tools_block,
)
from ronin_training.harvest.trajectory import (
    RecordedCall,
    RecordedResult,
    Step,
    ToolSchema,
    Trajectory,
)
from ronin_training.validators import load_registry


def _registry_schemas() -> tuple[ToolSchema, ...]:
    """Ronin's real v1 registry as harvest ToolSchemas — the same source of truth
    ``eval_runner`` and ``dataset_builder`` read."""
    return tuple(
        ToolSchema(name=t["name"], description=t["description"], parameters=t["parameters"])
        for t in load_registry().values()
    )


def test_the_tools_block_is_byte_identical_to_the_canonical_eval_renderer():
    ours = json.dumps(list(tools_block(_registry_schemas())), sort_keys=True, ensure_ascii=False)
    theirs = json.dumps(ronin_tools_for_template(), sort_keys=True, ensure_ascii=False)
    assert ours == theirs


def test_the_three_existing_tools_block_builders_still_agree():
    """Pins the drift this package chose to depend on rather than duplicate.

    ``eval_runner``, ``dataset_builder`` and ``synthetic_corpus`` each build the
    tools block independently. They agree today; the moment one changes shape, the
    harvest corpus is rendered under a prompt the eval does not use, which is
    divergence D1 all over again — so the disagreement must fail a test somewhere.
    """
    from ronin_training.dataset_builder import _full_registry_tools
    from ronin_training.synthetic_corpus import _tools_full_registry

    canonical = json.dumps(ronin_tools_for_template(), sort_keys=True)
    assert json.dumps(_full_registry_tools(), sort_keys=True) == canonical
    assert json.dumps(_tools_full_registry(), sort_keys=True) == canonical


def test_a_rendered_target_parses_back_to_exactly_the_recorded_calls():
    """Runtime parity: what we train as the target is what the runtime would execute."""
    step = Step(
        index=0,
        calls=(
            RecordedCall(id="c0", name="read", arguments={"path": "src/app/loader.py"}),
            RecordedCall(id="c1", name="read", arguments={"path": "src/app/store.py"}),
        ),
    )
    text = assistant_target_text(step)
    assert parse_target_text(text) == (
        ("read", {"path": "src/app/loader.py"}),
        ("read", {"path": "src/app/store.py"}),
    )


def test_the_target_text_is_the_v2_dialect_and_not_the_v1_renderer():
    """Byte-identity with ``ronin_dialect`` used to be the contract. It is now the bug.

    That renderer emits the bare v1 ``<tool_call>`` tag, which the v2 shim does not
    parse at all — so a target matching it is a target the runtime silently ignores.
    The parity that matters is with the runtime's own parser, asserted in
    ``test_harvest_v2_dialect.py``.
    """
    step = Step(
        index=0,
        calls=(
            RecordedCall(
                id="c0", name="write", arguments={"path": "a.py", "content": "x"}
            ),
        ),
    )
    rendered = assistant_target_text(step)
    assert rendered.startswith("<ronin:tool_call>")
    assert rendered != ronin_dialect.render_tool_call_message(
        [{"name": "write", "arguments": {"path": "a.py", "content": "x"}}]
    )


def test_tool_call_arguments_are_json_objects_not_encoded_strings():
    """Divergence D2: a string-typed arguments value contradicts the instruction the
    chat template gives the model at inference."""
    step = Step(
        index=0, calls=(RecordedCall(id="c0", name="read", arguments={"path": "a.py"}),)
    )
    message = assistant_message(step)
    arguments = message["tool_calls"][0]["function"]["arguments"]
    assert isinstance(arguments, dict)


def test_the_history_is_the_recorded_one_in_order():
    traj = Trajectory(
        task_id="t",
        run_id="r",
        prompt="do the thing",
        system="you are ronin",
        tools=(ToolSchema(name="read", description="read a file"),),
        steps=(
            Step(
                index=0,
                text="looking",
                calls=(RecordedCall(id="c0", name="read", arguments={"path": "a.py"}),),
                results=(RecordedResult(call_id="c0", name="read", ok=True, content="A = 1"),),
            ),
            Step(index=1, text="done"),
        ),
        verified=True,
    )
    payload = render_prefix(traj, 1)
    roles = [m["role"] for m in payload.messages]
    assert roles == ["system", "user", "assistant", "tool"]
    assert payload.messages[-1] == {"role": "tool", "tool_call_id": "c0", "content": "A = 1"}


def test_a_call_with_no_recorded_result_gets_no_invented_observation():
    step = Step(
        index=0,
        calls=(RecordedCall(id="c0", name="read", arguments={"path": "a.py"}),),
        results=(),
    )
    assert tool_messages(step) == ()


def test_a_failed_call_shows_the_model_the_error_it_actually_saw():
    step = Step(
        index=0,
        calls=(RecordedCall(id="c0", name="read", arguments={"path": "gone.py"}),),
        results=(
            RecordedResult(
                call_id="c0", name="read", ok=False, error="gone.py does not exist"
            ),
        ),
    )
    assert tool_messages(step)[0]["content"] == "gone.py does not exist"


def test_render_prefix_refuses_a_step_index_outside_the_trajectory():
    traj = Trajectory(task_id="t", run_id="r", prompt="p", steps=(Step(index=0),), verified=True)
    with pytest.raises(IndexError):
        render_prefix(traj, 2)


def test_a_payload_with_no_messages_is_refused():
    with pytest.raises(ValueError, match="messages cannot be empty"):
        PromptPayload(messages=())


def test_the_row_shape_is_the_pair_the_trainer_consumes():
    """``train_cuda.py`` renders ``apply_chat_template(row["messages"], tools=row["tools"])``,
    so the row may carry exactly those two keys and nothing the template would miss."""
    traj = Trajectory(
        task_id="t",
        run_id="r",
        prompt="p",
        tools=(ToolSchema(name="read", description="read a file"),),
        steps=(Step(index=0, text="hi"),),
        verified=True,
    )
    row = render_prefix(traj, 0).as_row()
    assert set(row) == {"messages", "tools"}


def test_a_toolschema_without_a_description_is_refused():
    """The description is what the model uses to choose the tool; a row rendered
    without one trains a different prompt than inference sends."""
    with pytest.raises(ValueError, match="no description"):
        ToolSchema(name="read", description="")
