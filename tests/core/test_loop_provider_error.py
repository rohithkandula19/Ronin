"""A provider that reports a failed turn must not look like a finished answer.

`FinalMessage` grew an `error` field because the loop seam had no way to say "the
provider ended this turn badly". Before it, the tool-call shim could exhaust its
repair budget, set `FinishReason.ERROR`, and the turn still arrived here as prose
with no tool calls — which is precisely the shape of a model that chose to answer.
The loop ended with `TurnState.DONE` and `StopReason.NO_TOOL_CALLS`, and nothing
anywhere told the user their local model had failed to emit a usable call.

Two outcomes are distinguished, and the distinction is the point:

* **no calls survived** — the turn is over and it failed: `Error`, then
  `TurnEnd(ERROR, "provider_error")`. Never `DONE`.
* **some calls survived** — the failure is reported *and* the good calls run.
  A model that asked for three files and mis-typed the fourth should not lose
  the three.
"""

from __future__ import annotations

from fakes import (
    FakeModel,
    FakePolicy,
    FakeTools,
    assistant_calls,
    call,
    seed,
    spec,
    succeeds,
)

from ronin.core.loop import run_turn
from ronin.core.protocols import FinalMessage
from ronin.core.types import (
    Error,
    Event,
    ToolEnd,
    TurnEnd,
    TurnState,
)


async def drain(state: object, model: FakeModel, tools: FakeTools) -> list[Event]:
    return [event async for event in run_turn(state, model, tools, FakePolicy())]  # type: ignore[arg-type]


def failed_turn(
    text: str, *calls: object, detail: str = "gave up after 2 repair attempt(s)"
) -> tuple[FinalMessage, ...]:
    """What the shim produces once its repair budget is spent."""
    return (
        FinalMessage(
            message=assistant_calls(*calls, text=text),  # type: ignore[arg-type]
            error=detail,
            notes=("unparseable tool call: no name key",),
        ),
    )


# --------------------------------------------------------------------------- #
# No calls survived
# --------------------------------------------------------------------------- #


async def test_a_failed_turn_with_no_calls_ends_in_error_not_done() -> None:
    """The regression this field exists for.

    `DONE` here is a lie the user acts on: they read the prose as the answer and
    never learn the model was asked three times for a tool call and never managed
    one.
    """
    model = FakeModel([failed_turn("I tried to call read_file")])
    events = await drain(
        seed(), model, FakeTools({"read_file": (spec("read_file"), succeeds("ok"))})
    )

    errors = [e for e in events if isinstance(e, Error)]
    assert len(errors) == 1
    assert "gave up after 2 repair attempt(s)" in errors[0].message
    assert errors[0].kind == "provider"
    assert not errors[0].recoverable, "nothing survived, so there is nothing to resume into"

    end = events[-1]
    assert isinstance(end, TurnEnd)
    assert end.state is TurnState.ERROR
    assert end.stop_reason == "provider_error"


async def test_the_model_prose_is_still_kept_so_the_user_can_see_what_it_tried() -> None:
    model = FakeModel([failed_turn("I'll read a.py for you")])
    events = await drain(seed(), model, FakeTools({}))
    end = events[-1]
    assert isinstance(end, TurnEnd)
    assert end.agent_state is not None, "a failed turn must still be resumable"
    assert "I'll read a.py" in end.agent_state.messages[-1].text


# --------------------------------------------------------------------------- #
# Some calls survived
# --------------------------------------------------------------------------- #


async def test_calls_that_parsed_still_run_and_the_failure_is_still_reported() -> None:
    """Both halves. Reporting without running punishes the model for its worst
    call; running without reporting hides a failure the user paid for."""
    model = FakeModel(
        [
            failed_turn("reading them", call("t1", "read_file", path="good.py")),
            (FinalMessage(message=assistant_calls(text="done")),),
        ]
    )
    tools = FakeTools({"read_file": (spec("read_file"), succeeds("contents"))})
    events = await drain(seed(), model, tools)

    errors = [e for e in events if isinstance(e, Error)]
    assert len(errors) == 1, "the failure was not surfaced"
    assert errors[0].recoverable, "work survived, so the turn can continue"

    ran = [e for e in events if isinstance(e, ToolEnd)]
    assert [e.name for e in ran] == ["read_file"], "the call that parsed was dropped"

    end = events[-1]
    assert isinstance(end, TurnEnd)
    assert end.state is not TurnState.ERROR, "the turn continued after the report"


# --------------------------------------------------------------------------- #
# The clean path is unchanged
# --------------------------------------------------------------------------- #


async def test_a_clean_turn_emits_no_error_event() -> None:
    """`error=""` is the default, so every existing adapter keeps its behaviour."""
    model = FakeModel([(FinalMessage(message=assistant_calls(text="the answer")),)])
    events = await drain(seed(), model, FakeTools({}))
    assert not [e for e in events if isinstance(e, Error)]
    end = events[-1]
    assert isinstance(end, TurnEnd)
    assert end.state is TurnState.DONE


async def test_notes_do_not_by_themselves_fail_a_turn() -> None:
    """Notes are caveats ("usage not reported by this runtime"), not failures.
    Treating them as errors would make every mlx turn look broken."""
    model = FakeModel(
        [
            (
                FinalMessage(
                    message=assistant_calls(text="fine"),
                    notes=("usage not reported by this runtime",),
                ),
            )
        ]
    )
    events = await drain(seed(), model, FakeTools({}))
    assert not [e for e in events if isinstance(e, Error)]
