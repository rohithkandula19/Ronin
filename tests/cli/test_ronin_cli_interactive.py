"""The integration test for this change: a gated call, approved by a human, that runs.

Every other test here covers one seam. This one covers the whole path, because the defect
being fixed was never in a seam — each piece worked and nothing connected them:

* the loop asks the injected policy whether a gated call may proceed;
* the policy asks its :class:`~ronin.safety.policy.Asker`;
* the asker in an interactive session is a :class:`~ronin.cli.approve.Handoff`;
* the Handoff puts the question to whatever UI attached itself.

Take any one of those away and the observable result is identical — the edit is refused —
which is why this watches the *tool* run rather than a mock being called. The policy
engine, the gate and the registry here are the shipped objects; only the model, the tool's
body and the human are fakes.
"""

from __future__ import annotations

from pathlib import Path

import stream_harness as h

from ronin.cli.approve import Handoff
from ronin.cli.spine import Runtime
from ronin.cli.stream import Conversation
from ronin.core.types import (
    ApprovalDecision,
    ApprovalRequest,
    DangerLevel,
    Event,
    Mode,
    ToolEnd,
)
from ronin.safety.policy import Decision

GATED = "edit"


def gated_writer(root: Path) -> h.FakeTool:
    """A tool that really writes, and that policy must stop before it does."""
    tool = h.writer(root, name=GATED)
    return h.FakeTool(
        name=tool.name,
        danger=DangerLevel.MUTATING,
        requires_approval=True,
        handler=tool.handler,
    )


def human(*answers: ApprovalDecision) -> tuple[Handoff, list[ApprovalRequest]]:
    """A Handoff with a scripted human attached, and the questions they were asked.

    Attaching is what the Textual app does on mount; answering from a list is what a
    person does with the keyboard. Running out of scripted answers denies, so a test that
    provokes an unexpected extra question fails on the tool not running rather than
    hanging.
    """
    asked: list[ApprovalRequest] = []
    queue = list(answers)
    handoff = Handoff()

    async def ui(request: ApprovalRequest) -> ApprovalDecision:
        asked.append(request)
        if not queue:
            return ApprovalDecision(approved=False, reason="the scripted human said nothing")
        return queue.pop(0)

    handoff.attach(ui)
    return handoff, asked


def session(tmp_path: Path, handoff: Handoff) -> tuple[h.ScriptedTools, Runtime]:
    """A real runtime whose policy asks, wired to ``handoff``.

    ``Mode.ASK`` matters: the harness defaults to ``auto_edit``, in which policy allows a
    mutating call *without* asking anyone. A test written against that default would show
    the tool running and prove nothing about approval at all.
    """
    loaded = h.build_loaded(tmp_path)
    tools = h.ScriptedTools([gated_writer(loaded.paths.workspace_root)])
    runtime = h.build_runtime(loaded, tools=tools, asker=handoff, default_decision=Decision.ASK)
    runtime.policy.set_mode(Mode.ASK)
    return tools, runtime


def gated_end(events: list[Event]) -> ToolEnd:
    """The gated tool's own ``ToolEnd``. The turn also emits synthetic ones for the
    checkpoint and verify steps, and asserting on a count would break the day another
    step is added."""
    ends = [event for event in events if isinstance(event, ToolEnd) and event.name == GATED]
    assert len(ends) == 1, f"expected one {GATED} result, saw {len(ends)}"
    return ends[0]


async def run_turn(
    tmp_path: Path, handoff: Handoff, *, prompt: str = "write notes.txt"
) -> tuple[list[Event], h.ScriptedTools]:
    tools, runtime = session(tmp_path, handoff)
    model = h.ScriptedModel(
        [h.call(GATED, {"file_path": "notes.txt", "content": "written"}), h.say("done")]
    )
    conversation = Conversation(model=model)
    seen = [event async for event in conversation.run_prompt(runtime, prompt)]
    return seen, tools


async def test_an_approved_call_actually_runs(tmp_path: Path) -> None:
    """The behaviour that did not exist anywhere in this codebase before the change."""
    handoff, asked = human(ApprovalDecision(approved=True))
    events, tools = await run_turn(tmp_path, handoff)

    assert [request.name for request in asked] == [GATED], "the human was asked"
    assert [use.name for use in tools.executed] == [GATED], "and the tool then ran"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "written"
    assert gated_end(events).result.ok


async def test_a_refused_call_does_not_run_and_says_who_refused_it(tmp_path: Path) -> None:
    handoff, asked = human(ApprovalDecision(approved=False, reason="not that file"))
    events, tools = await run_turn(tmp_path, handoff)

    assert len(asked) == 1
    assert tools.executed == [], "a denied tool must not execute"
    assert not (tmp_path / "notes.txt").exists()
    end = gated_end(events)
    assert not end.result.ok
    assert "not that file" in (end.result.error or ""), "the human's words reach the model"


async def test_with_no_ui_attached_the_call_is_refused(tmp_path: Path) -> None:
    """The fail-closed case, and the one that must keep behaving exactly as it did before
    an answer path existed at all."""
    handoff = Handoff()
    events, tools = await run_turn(tmp_path, handoff)

    assert not handoff.attached
    assert tools.executed == []
    assert not (tmp_path / "notes.txt").exists()
    assert not gated_end(events).result.ok


async def test_the_human_is_asked_with_the_text_the_loop_rendered(tmp_path: Path) -> None:
    """What is shown is what runs. The request handed to the UI carries the loop's own
    ``rendered`` string, so the modal cannot be showing one thing while another executes.
    """
    handoff, asked = human(ApprovalDecision(approved=True))
    await run_turn(tmp_path, handoff)

    assert asked[0].rendered, "a request with nothing to show would be unapprovable"
    assert "notes.txt" in asked[0].rendered
    assert asked[0].danger_level is DangerLevel.MUTATING


async def test_auto_accept_mode_does_not_ask_the_human_at_all(tmp_path: Path) -> None:
    """The defect this design avoids, pinned as a test.

    The loop emits ``ApprovalRequest`` for every tool whose spec requires approval, *then*
    asks the policy — and in auto-accept mode the policy allows a mutating call itself. A
    UI driven by the event would raise a modal here, in the one mode whose whole purpose
    is not raising them. Driven by the policy's question, nothing is asked and the call
    still runs.
    """
    handoff, asked = human(ApprovalDecision(approved=True))
    tools, runtime = session(tmp_path, handoff)
    runtime.policy.set_mode(Mode.AUTO_EDIT)
    model = h.ScriptedModel(
        [h.call(GATED, {"file_path": "auto.txt", "content": "no prompt"}), h.say("done")]
    )
    conversation = Conversation(model=model)
    async for _event in conversation.run_prompt(runtime, "edit it"):
        pass

    assert asked == [], "auto-accept mode asked a human anyway"
    assert [use.name for use in tools.executed] == [GATED], "and the call still ran"
    assert (tmp_path / "auto.txt").read_text(encoding="utf-8") == "no prompt"


async def test_remembering_an_approval_stops_the_second_question(tmp_path: Path) -> None:
    """What the ``a`` key buys, checked where it takes effect rather than where it is
    mapped. Only one answer is scripted: if the session rule the first approval created
    did not exist, the second question would be answered by the fallback denial and the
    second call would not run."""
    handoff, asked = human(ApprovalDecision(approved=True, remember=True))
    tools, runtime = session(tmp_path, handoff)
    model = h.ScriptedModel(
        [
            h.call(GATED, {"file_path": "one.txt", "content": "1"}, use_id="c1"),
            h.call(GATED, {"file_path": "one.txt", "content": "2"}, use_id="c2"),
            h.say("done"),
        ]
    )
    conversation = Conversation(model=model)
    async for _event in conversation.run_prompt(runtime, "write it twice"):
        pass

    assert len(asked) == 1, "the human was asked twice despite saying 'and remember'"
    assert [use.name for use in tools.executed] == [GATED, GATED], "both calls ran"
    assert (tmp_path / "one.txt").read_text(encoding="utf-8") == "2"
