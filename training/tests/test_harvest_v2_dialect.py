"""The corpus must speak the dialect the v2 runtime parses. Nothing else counts.

`training/` renders training targets; `src/ronin/providers/shim.py` parses them at
inference. If those two disagree the fine-tune is worthless in a way that is almost
impossible to diagnose from the outside: every tool call fails, so it reads as "the
adapter did not learn", when in fact the corpus was written in another language.

That was the live state. The harvest renderer emitted the bare v1 ``<tool_call>`` tag
via ``packages/dialect``, while the v2 shim recognises ``<ronin:tool_call>`` and only
that — namespaced on purpose, because the bare tag collides with several base models'
own chat templates. Demonstrated below rather than asserted: the v1 rendering produces
**zero** calls and leaks its raw tag to the user as prose.

These tests are the standing guard. They import the real shim, so they fail the moment
the two sides drift again.
"""
from __future__ import annotations

import json

import pytest
from ronin_training.harvest import prompt as harvest
from ronin_training.harvest.trajectory import RecordedCall, Step

shim = pytest.importorskip(
    "ronin.providers.shim",
    reason="the v2 runtime is not importable here; the parity guard needs it",
)


def parsed(text: str) -> list[tuple[str, dict[str, object]]]:
    """What the **runtime** would execute, straight through the real shim parser."""
    emit = shim.ShimStreamParser().feed(text)
    return [(call.name, json.loads(call.raw_arguments)) for call in emit.calls]


def test_the_tags_are_byte_identical_to_the_runtimes() -> None:
    """`training/` copies the tags rather than importing them, so the copy must be pinned.

    The copy exists because the training package has to install on a bare Colab runtime
    where ``ronin`` is not on the path. That is a good reason to duplicate a constant and
    a terrible reason to let it rot, so this is the test that makes the duplication safe.
    """
    assert harvest.OPEN_TAG == shim.OPEN_TAG
    assert harvest.CLOSE_TAG == shim.CLOSE_TAG


def test_a_rendered_target_is_executed_by_the_real_runtime_parser() -> None:
    step = Step(
        index=0,
        text="reading the config first",
        calls=(
            RecordedCall(id="c1", name="read", arguments={"path": "src/app.py"}),
            RecordedCall(id="c2", name="bash", arguments={"command": "pytest -q", "timeout": 60}),
        ),
    )
    text = harvest.assistant_target_text(step)

    assert parsed(text) == [
        ("read", {"path": "src/app.py"}),
        ("bash", {"command": "pytest -q", "timeout": 60}),
    ]
    emit = shim.ShimStreamParser().feed(text)
    assert list(emit.failures) == [], "a target the runtime cannot parse is not a target"
    assert emit.visible.strip() == "reading the config first", "prose must survive"


def test_the_v1_dialect_produces_no_calls_at_all() -> None:
    """The bug, pinned as a fact rather than a warning in a docstring.

    Not "fewer calls" or "some failures" — *zero*, with the tag leaking to the user as
    prose. This is why the dialect had to be decided before anything was trained.
    """
    v1 = '<tool_call>{"name": "read_file", "arguments": {"path": "src/app.py"}}</tool_call>'
    emit = shim.ShimStreamParser().feed(v1)
    assert list(emit.calls) == []
    assert "<tool_call>" in emit.visible, "it is not executed *and* not hidden"


def test_arguments_are_a_json_object_never_a_string() -> None:
    """The D2 divergence: a JSON-encoded string here trains double-encoding."""
    step = Step(
        index=0,
        text="",
        calls=(RecordedCall(id="c1", name="write", arguments={"path": "a.txt", "content": "hi"}),),
    )
    block = harvest.assistant_target_text(step)
    payload = json.loads(block[len(harvest.OPEN_TAG) : -len(harvest.CLOSE_TAG)])
    assert isinstance(payload["arguments"], dict)


def test_a_turn_with_no_calls_renders_as_plain_prose() -> None:
    step = Step(index=0, text="nothing to do here", calls=())
    assert harvest.assistant_target_text(step) == "nothing to do here"
    assert parsed(harvest.assistant_target_text(step)) == []


def test_the_renderer_round_trips_through_its_own_parser() -> None:
    """`parse_target_text` and the shim must agree, or one of them is lying."""
    step = Step(
        index=0,
        text="fixing it",
        calls=(RecordedCall(id="c1", name="edit", arguments={"path": "x.py", "old": "a"}),),
    )
    text = harvest.assistant_target_text(step)
    mine = [(name, dict(args)) for name, args in harvest.parse_target_text(text)]
    assert mine == parsed(text)


def test_every_tool_name_in_the_shipped_registry_is_a_v2_name() -> None:
    """The registry file is generated from the live registry; this pins the outcome.

    v1 named them ``read_file``/``write_file``/``run_command``. A corpus validated
    against those trains a model to call tools that do not exist.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    payload = json.loads((root / "training" / "config" / "tool_registry_v2.json").read_text())
    names = {tool["name"] for tool in payload["tools"]}

    assert {"read", "write", "edit", "bash"} <= names
    assert not {name for name in names if name.endswith("_file")}
    assert "run_command" not in names
