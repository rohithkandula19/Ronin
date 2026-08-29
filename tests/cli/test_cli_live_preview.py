"""The diff a human reads before approving an edit.

`render_diff` was written, tested and complete, and had no caller outside the demo:
the live approval prompt showed `edit({"new_string": …, "old_string": …})` as a JSON
blob, while the README said in three places that every edit is gated behind a diff
preview. These tests are what makes that sentence true.

The reach is the same two hops `live_todos` makes — gate → inner registry → context —
and it degrades the same way, because an approval that raises is worse than an
approval that reads a little worse.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import stream_harness as h

from ronin.cli.approve import Handoff
from ronin.cli.gate import live_preview
from ronin.cli.stream import Conversation
from ronin.core.types import ApprovalDecision, ApprovalRequest, Mode, ToolUse
from ronin.safety.policy import Decision
from ronin.tools.base import ToolContext
from ronin.tools.files import EditTool, MultiEditTool, WriteTool
from ronin.tools.registry import ToolRegistry


def registry(root: Path) -> ToolRegistry:
    return ToolRegistry((EditTool(), WriteTool(), MultiEditTool()), ToolContext(root=root))


def gate(reg: ToolRegistry) -> object:
    class Gate:
        inner = reg

    return Gate()


def test_an_edit_is_shown_as_a_diff_rather_than_as_its_arguments() -> None:
    """What this is all for.

    Before, a human approving an edit read the tool call and its JSON arguments and had
    to imagine the result. The diff is the result.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "a.py").write_text("def hello():\n    return 1\n")
        render = live_preview(gate(registry(root)))

        shown = render(
            ToolUse(
                id="c1",
                name="edit",
                arguments={"path": "a.py", "old_string": "return 1", "new_string": "return 2"},
            )
        )

        assert shown is not None
        assert "--- a/a.py" in shown
        assert "-    return 1" in shown
        assert "+    return 2" in shown
        # The unchanged line is context, not a change.
        assert " def hello():" in shown


def test_a_new_file_is_shown_as_its_whole_contents() -> None:
    # Creating a file is an edit from nothing, and the thing worth reading is what will
    # be in it.
    with tempfile.TemporaryDirectory() as directory:
        render = live_preview(gate(registry(Path(directory))))
        shown = render(
            ToolUse(id="c1", name="write", arguments={"path": "new.py", "content": "print(1)\n"})
        )
        assert shown is not None
        assert "+print(1)" in shown


def test_an_overwrite_is_shown_against_what_is_already_there() -> None:
    """The case the `write` guard exists for. A whole-file overwrite chosen without
    seeing the file destroys work, so the one thing the prompt must show is what is
    being destroyed."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "a.py").write_text("important\n")
        render = live_preview(gate(registry(root)))
        shown = render(
            ToolUse(id="c1", name="write", arguments={"path": "a.py", "content": "replaced\n"})
        )
        assert shown is not None
        assert "-important" in shown
        assert "+replaced" in shown


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("run", {"command": "rm -rf /"}),
        ("read", {"path": "a.py"}),
        ("edit", {"path": "gone.py", "old_string": "a", "new_string": "b"}),
        ("edit", {"path": "a.py", "old_string": "not in the file", "new_string": "b"}),
    ],
    ids=["not an edit", "a read", "file missing", "no match"],
)
def test_a_call_with_no_diff_to_show_declines(name: str, arguments: dict[str, str]) -> None:
    """`None` hands the loop back to the JSON line it used before there was a diff.

    Silently showing a *stale* or half-built diff would be worse than showing no diff:
    the human would approve something that does not correspond to what runs.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "a.py").write_text("contents\n")
        render = live_preview(gate(registry(root)))
        assert render(ToolUse(id="c1", name=name, arguments=arguments)) is None


@pytest.mark.parametrize(
    "registry_like",
    [object(), type("NoCtx", (), {"inner": object()})()],
    ids=["no inner", "inner without a context"],
)
def test_it_degrades_to_no_diff_rather_than_raising(registry_like: object) -> None:
    # Both hops are optional, exactly as they are for the plan. A registry assembled
    # without a context must cost the diff, not the approval.
    render = live_preview(registry_like)
    assert render(ToolUse(id="c1", name="edit", arguments={"path": "a.py"})) is None


def test_the_diff_carries_no_terminal_control_characters() -> None:
    """Rendered plain on purpose.

    `render_approval` puts `rendered` through `styles.text`, which strips control
    characters so that approval text cannot paint over the prompt a human is reading.
    Colour added here would be removed there, so asking for it would only promise a
    highlighting that does not survive the trip.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "a.py").write_text("before\n")
        render = live_preview(gate(registry(root)))
        shown = render(
            ToolUse(
                id="c1",
                name="edit",
                arguments={"path": "a.py", "old_string": "before", "new_string": "after"},
            )
        )
        assert shown is not None
        assert "\x1b" not in shown


# --------------------------------------------------------------------------- #
# The wiring, driven through a real turn
# --------------------------------------------------------------------------- #


async def test_a_real_turn_asks_the_human_with_a_diff_not_a_json_blob() -> None:
    """The test that holds the wiring in place.

    Everything above proves a diff *can* be built. This proves the running program
    actually asks for one: drop the `preview=` argument in `Conversation._turn` and the
    prompt silently goes back to `write({"content": …, "path": …})`, with every other
    test in the suite still green. That single line is the whole feature from a user's
    side, and it had nothing holding it down.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        loaded = h.build_loaded(root)
        workspace = loaded.paths.workspace_root
        (workspace / "a.py").write_text("def hello():\n    return 1\n")

        asked: list[ApprovalRequest] = []
        handoff = Handoff()

        async def ui(request: ApprovalRequest) -> ApprovalDecision:
            asked.append(request)
            return ApprovalDecision(approved=False, reason="")

        handoff.attach(ui)

        tools = ToolRegistry((EditTool(),), ToolContext(root=workspace))
        runtime = h.build_runtime(loaded, tools=tools, asker=handoff, default_decision=Decision.ASK)
        runtime.policy.set_mode(Mode.ASK)
        model = h.ScriptedModel(
            [
                h.call(
                    "edit",
                    {"path": "a.py", "old_string": "return 1", "new_string": "return 2"},
                ),
                h.say("done"),
            ]
        )

        conversation = Conversation(model=model)
        async for _event in conversation.run_prompt(runtime, "change the return"):
            pass

        assert asked, "the human was never asked"
        shown = asked[0].rendered
        assert "--- a/a.py" in shown, f"expected a diff, got: {shown!r}"
        assert "-    return 1" in shown
        assert "+    return 2" in shown
        assert not shown.startswith("edit({"), "fell back to the JSON line"
