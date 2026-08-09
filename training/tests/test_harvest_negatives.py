"""Each of the four negative classes, mined from a real recorded transcript.

"Real recorded" is load-bearing: the trajectories here are written by
``ronin.persistence.Transcript`` from real ``ronin.core.types`` events and read back
through ``read_events``. A miner that only works on hand-built dicts is a miner that
will find nothing in the eval suite's output.

Two tests pin the miner to the harness rather than to my reading of it: the loop's own
``fingerprint`` and the denial text produced by running the **real** ``run_turn`` with
a refusing policy.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest
from ronin_training.harvest.negatives import (
    DEFAULT_TOOL_ROLES,
    LOOP_REPEATS,
    LOOP_WINDOW,
    NegativeClass,
    ToolRoles,
    class_counts,
    mine,
)
from ronin_training.harvest.trajectory import (
    DENIAL_PREFIX,
    RecordedCall,
    RecordedResult,
    Step,
    Trajectory,
    fingerprint,
)
from test_harvest_helpers import load_recorded, registry_tools, turn

from ronin.core.loop import STALL_REPEATS, STALL_WINDOW, run_turn
from ronin.core.loop import fingerprint as loop_fingerprint
from ronin.core.protocols import FinalMessage, ModelChunk
from ronin.core.types import (
    AgentState,
    ApprovalDecision,
    Budget,
    DangerLevel,
    Message,
    Role,
    Text,
    ToolResult,
    ToolSpec,
    ToolUse,
)

_TOOLS = registry_tools("read_file", "write_file", "edit_file")
_DENIAL = "the user declined this action: leave the vendored tree alone"


# --------------------------------------------------------------------------- #
# The four classes, each from a real recorded transcript
# --------------------------------------------------------------------------- #


def test_malformed_tool_json_is_mined_from_an_unparseable_block(tmp_path):
    events = turn(
        index=0,
        text='Reading it.\n<tool_call>\n{"name": "read_file", "arguments": {path: }}\n</tool_call>',
    )
    traj = load_recorded(
        tmp_path, "run-malformed", events, task_id="t", prompt="read a.py", tools=_TOOLS
    )
    found = [n for n in mine(traj) if n.kind is NegativeClass.MALFORMED_TOOL_JSON]
    assert len(found) == 1
    assert found[0].evidence["defect"] == "unparseable"
    assert "<tool_call>" in found[0].evidence["block"]


def test_malformed_tool_json_is_mined_from_a_wrong_typed_argument(tmp_path):
    """``write_file`` declares ``content`` a string; an integer is a wrong type the
    real registry schema rejects."""
    events = turn(
        index=0,
        calls=[("c0", "write_file", {"path": "a.py", "content": 5})],
        results=[("c0", "write_file", False, "", "content must be a string")],
    )
    traj = load_recorded(
        tmp_path, "run-typed", events, task_id="t", prompt="write a.py", tools=_TOOLS
    )
    found = [n for n in mine(traj) if n.kind is NegativeClass.MALFORMED_TOOL_JSON]
    assert len(found) == 1
    assert found[0].evidence["defect"] == "schema"
    assert found[0].call_id == "c0"


def test_malformed_tool_json_is_mined_from_a_missing_required_key(tmp_path):
    events = turn(
        index=0,
        calls=[("c0", "write_file", {"path": "a.py"})],
        results=[("c0", "write_file", False, "", "content is required")],
    )
    traj = load_recorded(
        tmp_path, "run-missing", events, task_id="t", prompt="write a.py", tools=_TOOLS
    )
    found = [n for n in mine(traj) if n.kind is NegativeClass.MALFORMED_TOOL_JSON]
    assert len(found) == 1
    assert "content" in found[0].detail


def test_a_schema_valid_call_is_not_mined_as_malformed(tmp_path):
    events = turn(
        index=0,
        calls=[("c0", "read_file", {"path": "a.py"})],
        results=[("c0", "read_file", True, "A = 1", "")],
    )
    traj = load_recorded(
        tmp_path, "run-clean", events, task_id="t", prompt="read a.py", tools=_TOOLS
    )
    assert class_counts(mine(traj))["malformed_tool_json"] == 0


def test_ignoring_an_approval_denial_is_mined_from_a_recorded_gate_refusal(tmp_path):
    args = {"path": "vendor/x.py", "content": "1\n"}
    events = [
        *turn(
            index=0,
            calls=[("c0", "write_file", args)],
            results=[("c0", "write_file", False, "", f"{DENIAL_PREFIX}{_DENIAL}")],
            gated=["c0"],
        ),
        *turn(
            index=1,
            text="Trying that again.",
            calls=[("c1", "write_file", dict(args))],
            results=[("c1", "write_file", False, "", f"{DENIAL_PREFIX}{_DENIAL}")],
            gated=["c1"],
        ),
    ]
    traj = load_recorded(
        tmp_path, "run-denial", events, task_id="t", prompt="patch vendor", tools=_TOOLS
    )
    found = [n for n in mine(traj) if n.kind is NegativeClass.IGNORED_DENIAL]
    assert len(found) == 1
    assert found[0].step_index == 1
    assert found[0].evidence["denial_reason"] == _DENIAL
    assert found[0].evidence["denied_at"] == 0


def test_adapting_after_a_denial_is_not_mined(tmp_path):
    """The behaviour the corpus wants to reward must not be mined as a failure."""
    events = [
        *turn(
            index=0,
            calls=[("c0", "write_file", {"path": "vendor/x.py", "content": "1\n"})],
            results=[("c0", "write_file", False, "", f"{DENIAL_PREFIX}{_DENIAL}")],
            gated=["c0"],
        ),
        *turn(
            index=1,
            calls=[("c1", "write_file", {"path": "src/x.py", "content": "1\n"})],
            results=[("c1", "write_file", True, "wrote 1 file", "")],
            gated=["c1"],
        ),
    ]
    traj = load_recorded(
        tmp_path, "run-adapted", events, task_id="t", prompt="patch it", tools=_TOOLS
    )
    assert class_counts(mine(traj))["ignored_denial"] == 0


def test_looping_is_mined_at_the_third_identical_action(tmp_path):
    events: list[object] = []
    for i in range(3):
        events.extend(
            turn(
                index=i,
                calls=[(f"c{i}", "read_file", {"path": "a.py"})],
                results=[(f"c{i}", "read_file", True, "A = 1", "")],
            )
        )
    traj = load_recorded(
        tmp_path, "run-loop", events, task_id="t", prompt="read a.py", tools=_TOOLS
    )
    found = [n for n in mine(traj) if n.kind is NegativeClass.LOOPING]
    assert len(found) == 1
    assert found[0].step_index == 2
    assert found[0].evidence["repeats"] == LOOP_REPEATS


def test_two_identical_actions_are_not_yet_a_loop(tmp_path):
    events: list[object] = []
    for i in range(2):
        events.extend(
            turn(
                index=i,
                calls=[(f"c{i}", "read_file", {"path": "a.py"})],
                results=[(f"c{i}", "read_file", True, "A = 1", "")],
            )
        )
    traj = load_recorded(
        tmp_path, "run-twice", events, task_id="t", prompt="read a.py", tools=_TOOLS
    )
    assert class_counts(mine(traj))["looping"] == 0


def test_editing_without_reading_is_mined(tmp_path):
    events = turn(
        index=0,
        calls=[("c0", "edit_file", {"path": "src/a.py", "old_string": "1", "new_string": "2"})],
        results=[("c0", "edit_file", False, "", "src/a.py has not been read in this session")],
    )
    traj = load_recorded(
        tmp_path, "run-blind", events, task_id="t", prompt="patch a.py", tools=_TOOLS
    )
    found = [n for n in mine(traj) if n.kind is NegativeClass.EDIT_WITHOUT_READ]
    assert len(found) == 1
    assert found[0].evidence["path"] == "src/a.py"
    assert "read src/a.py before writing it" in found[0].rule


def test_reading_before_editing_is_not_mined(tmp_path):
    events = [
        *turn(
            index=0,
            calls=[("c0", "read_file", {"path": "src/a.py"})],
            results=[("c0", "read_file", True, "x = 1", "")],
        ),
        *turn(
            index=1,
            calls=[("c1", "edit_file", {"path": "src/a.py", "old_string": "1", "new_string": "2"})],
            results=[("c1", "edit_file", True, "1 replacement", "")],
        ),
    ]
    traj = load_recorded(
        tmp_path, "run-careful", events, task_id="t", prompt="patch it", tools=_TOOLS
    )
    assert class_counts(mine(traj))["edit_without_read"] == 0


def test_a_successful_write_counts_as_a_read_for_the_next_edit(tmp_path):
    """Two nuances at once. The write succeeded, so the file did not exist and creating
    it was ordinary work — not a violation. And it now counts as read, so the following
    edit of the file the model just wrote is not a violation either."""
    events = [
        *turn(
            index=0,
            calls=[("c0", "write_file", {"path": "src/new.py", "content": "x = 1\n"})],
            results=[("c0", "write_file", True, "wrote 1 file", "")],
            gated=["c0"],
        ),
        *turn(
            index=1,
            calls=[
                ("c1", "edit_file",
                 {"path": "src/new.py", "old_string": "1", "new_string": "2"}),
            ],
            results=[("c1", "edit_file", True, "1 replacement", "")],
        ),
    ]
    traj = load_recorded(
        tmp_path, "run-write-then-edit", events, task_id="t", prompt="p", tools=_TOOLS
    )
    assert [n.step_index for n in mine(traj) if n.kind is NegativeClass.EDIT_WITHOUT_READ] == []


def test_a_denied_write_does_not_count_as_a_read(tmp_path):
    """A refused write never saw the file, so a following edit is still blind."""
    events = [
        *turn(
            index=0,
            calls=[("c0", "write_file", {"path": "src/a.py", "content": "x\n"})],
            results=[("c0", "write_file", False, "", f"{DENIAL_PREFIX}{_DENIAL}")],
            gated=["c0"],
        ),
        *turn(
            index=1,
            calls=[("c1", "edit_file", {"path": "src/a.py", "old_string": "1", "new_string": "2"})],
            results=[("c1", "edit_file", False, "", "not read in this session")],
        ),
    ]
    traj = load_recorded(
        tmp_path, "run-denied-write", events, task_id="t", prompt="p", tools=_TOOLS
    )
    assert [n.step_index for n in mine(traj) if n.kind is NegativeClass.EDIT_WITHOUT_READ] == [0, 1]


def test_class_counts_shows_a_zero_class_as_zero():
    clean = Trajectory(task_id="t", run_id="r", prompt="p", steps=(Step(index=0, text="hi"),))
    assert class_counts(mine(clean)) == {str(k): 0 for k in NegativeClass}


def test_the_tool_names_are_configurable_because_two_registries_shipped(tmp_path):
    """v1 spells the editor ``edit_file``, v2 spells it ``edit``. A miner configured for
    only one goes silently blind on the other's transcripts."""
    events = turn(
        index=0,
        calls=[("c0", "edit_file", {"path": "src/a.py", "old_string": "1", "new_string": "2"})],
        results=[("c0", "edit_file", True, "1 replacement", "")],
    )
    traj = load_recorded(tmp_path, "run-roles", events, task_id="t", prompt="p", tools=_TOOLS)
    v2_only = ToolRoles(
        readers=frozenset({"read"}),
        writers=frozenset({"edit", "multi_edit"}),
        overwriters=frozenset({"write"}),
    )
    assert class_counts(mine(traj, roles=v2_only))["edit_without_read"] == 0
    assert class_counts(mine(traj, roles=DEFAULT_TOOL_ROLES))["edit_without_read"] == 1


def test_creating_a_new_file_is_not_an_edit_without_read(tmp_path):
    """``write`` refuses only when the file already exists (``files.py``: ``if
    path.exists() and not ctx.has_been_read(path)``). A write that went through created
    a new file, so calling it a violation would fabricate a fact the run does not carry.
    Measured on the shipped corpus, the naive rule gave 127 false positives to 33 real."""
    events = turn(
        index=0,
        calls=[("c0", "write_file", {"path": "src/brand_new.py", "content": "x = 1\n"})],
        results=[("c0", "write_file", True, "wrote 1 file", "")],
    )
    traj = load_recorded(tmp_path, "run-create", events, task_id="t", prompt="p", tools=_TOOLS)
    assert class_counts(mine(traj))["edit_without_read"] == 0


def test_a_write_the_guard_refused_is_mined(tmp_path):
    """The refusal is the harness saying the file existed and had not been read — the
    one signal that distinguishes an overwrite from a creation."""
    events = turn(
        index=0,
        calls=[("c0", "write_file", {"path": "src/existing.py", "content": "x = 1\n"})],
        results=[
            (
                "c0",
                "write_file",
                False,
                "",
                "src/existing.py already exists and has not been read in this session",
            )
        ],
    )
    traj = load_recorded(tmp_path, "run-clobber", events, task_id="t", prompt="p", tools=_TOOLS)
    assert class_counts(mine(traj))["edit_without_read"] == 1


def test_an_edit_that_slipped_through_is_still_a_violation(tmp_path):
    """An edit requires a prior read unconditionally, so a run whose harness did not
    enforce it still demonstrates the wrong behaviour and must still be mined."""
    events = turn(
        index=0,
        calls=[("c0", "edit_file", {"path": "src/a.py", "old_string": "1", "new_string": "2"})],
        results=[("c0", "edit_file", True, "1 replacement", "")],
    )
    traj = load_recorded(tmp_path, "run-slip", events, task_id="t", prompt="p", tools=_TOOLS)
    assert class_counts(mine(traj))["edit_without_read"] == 1


# --------------------------------------------------------------------------- #
# Pins to the harness itself
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"path": "a.py"},
        {"b": 2, "a": 1},
        {"a": 1, "b": 2},
        {"nested": {"z": 1, "y": [1, 2]}},
    ],
)
def test_the_fingerprint_matches_the_loops_own_rule(arguments):
    """The training package restates ``ronin.core.loop.fingerprint`` because it must
    install without the agent tree. This is the test that keeps the copy honest."""
    assert fingerprint("read_file", arguments) == loop_fingerprint(
        ToolUse(id="x", name="read_file", arguments=arguments)
    )


def test_the_loop_thresholds_this_miner_mirrors_are_the_loops_actual_thresholds():
    assert (LOOP_REPEATS, LOOP_WINDOW) == (STALL_REPEATS, STALL_WINDOW)


class _ScriptedModel:
    """A ModelClient that emits one prepared assistant message. No network, no model."""

    def __init__(self, message: Message) -> None:
        self._message = message

    def stream(
        self, *, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> AsyncIterator[ModelChunk]:
        async def gen() -> AsyncIterator[ModelChunk]:
            yield FinalMessage(message=self._message)

        return gen()


class _OneTool:
    def __init__(self, spec: ToolSpec) -> None:
        self._spec = spec

    def specs(self) -> Sequence[ToolSpec]:
        return (self._spec,)

    def get(self, name: str) -> ToolSpec | None:
        return self._spec if name == self._spec.name else None

    async def execute(self, use: ToolUse) -> ToolResult:  # pragma: no cover - never reached
        raise AssertionError("a denied call must never be executed")


class _AlwaysDeny:
    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def approve(
        self, spec: ToolSpec, use: ToolUse, *, rendered: str
    ) -> ApprovalDecision:
        return ApprovalDecision(approved=False, reason=self._reason)

    def check_budget(self, budget: Budget) -> str | None:
        return None

    def cancelled(self) -> bool:
        return False


async def test_the_denial_marker_is_the_one_the_real_loop_writes():
    """Runs the real ``run_turn`` with a refusing policy and asserts this package's
    ``DENIAL_PREFIX`` recognises the result. A marker guessed from reading the source
    would silently stop matching the day the loop rewords it; this will not."""
    spec = ToolSpec(
        name="write_file",
        description="write a file",
        json_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        danger_level=DangerLevel.MUTATING,
        requires_approval=True,
    )
    message = Message(
        role=Role.ASSISTANT,
        content_blocks=(
            Text(text="writing"),
            ToolUse(id="c0", name="write_file", arguments={"path": "a.py"}),
        ),
    )
    denials: list[str] = []
    turns = run_turn(
        AgentState(messages=(Message(role=Role.USER, content_blocks=(Text(text="go"),)),)),
        _ScriptedModel(message),
        _OneTool(spec),
        _AlwaysDeny("leave the vendored tree alone"),
    )
    # Stop at the first refusal: the scripted model repeats itself forever, and the
    # real loop would (correctly) abort it as a stall, which is a different test.
    async for event in turns:
        result = getattr(event, "result", None)
        if result is not None and not result.ok:
            denials.append(result.error)
            break
    await turns.aclose()
    assert denials, "the scripted turn should have produced a refused call"
    recorded = RecordedResult(call_id="c0", name="write_file", ok=False, error=denials[0])
    assert recorded.denied
    assert recorded.denial_reason == "leave the vendored tree alone"


def test_a_non_denial_failure_is_not_read_as_a_denial():
    result = RecordedResult(
        call_id="c0", name="read_file", ok=False, error="a.py does not exist"
    )
    assert not result.denied
    assert result.denial_reason == ""


def test_mining_is_ordered_by_step_so_a_report_reads_chronologically():
    traj = Trajectory(
        task_id="t",
        run_id="r",
        prompt="p",
        tools=_TOOLS,
        steps=(
            Step(
                index=0,
                calls=(
                    RecordedCall(
                        id="a", name="edit_file",
                        arguments={"path": "x.py", "old_string": "1", "new_string": "2"},
                    ),
                ),
            ),
            Step(
                index=1,
                calls=(
                    RecordedCall(
                        id="b", name="edit_file",
                        arguments={"path": "y.py", "old_string": "1", "new_string": "2"},
                    ),
                ),
            ),
        ),
    )
    assert [n.step_index for n in mine(traj)] == [0, 1]
