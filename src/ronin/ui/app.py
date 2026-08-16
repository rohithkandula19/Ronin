"""The Textual app — the only module here allowed a third-party import, lazily.

Everything that decides anything lives in :mod:`ronin.ui.reduce` and
:mod:`ronin.ui.render` and is unit-tested without a terminal. This module binds
keys and paints: it folds each event into a :class:`~ronin.ui.reduce.ViewState`,
asks :func:`~ronin.ui.render.render_panels` for the five regions, and writes them
into widgets. There is no branch here that a test cannot reach through a pure
function.

Textual is imported **inside** :func:`run_app`, never at module scope, for two
reasons: ``python -m ronin.ui.demo`` and the whole headless path must work on a
bare install, and ``tests/ui`` must run with no Textual present. Absence is
reported (:data:`TEXTUAL_MISSING`), never guessed at.

Keys, and where their logic lives:

============= ===============================================================
``esc``       interrupt now — :func:`ronin.ui.reduce.press_escape`
``esc esc``   rewind to an earlier turn, within
              :data:`ronin.ui.reduce.DOUBLE_ESCAPE_WINDOW_SECONDS`
``shift+tab`` cycle normal → auto-accept → plan — :func:`ronin.ui.reduce.next_mode`
``ctrl+c``    quit
============= ===============================================================

The app never approves anything by itself. An :class:`~ronin.core.types.ApprovalRequest`
takes the whole screen as a modal that renders ``request.rendered`` verbatim, and the
keystroke is turned into a decision by :func:`ronin.ui.reduce.decision_for` — a pure
function in a module with no terminal in it. This layer chooses nothing; it carries the
human's answer back to whoever asked, and the policy engine is the only thing that may
act on it. A UI that answered on its own behalf would be exactly the auto-approval this
codebase refuses, and with ``on_attach`` unset that is enforced by there being no path
from a keystroke to an approval at all.

The modal is raised by the policy's *question*, never by the ``ApprovalRequest`` event —
see :class:`Session`. Getting that backwards would interrupt for every edit in
auto-accept mode.
"""

from __future__ import annotations

import asyncio
import importlib.util
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ronin.core.types import ApprovalDecision, ApprovalRequest, Event, Mode, TurnEnd

from .reduce import (
    SPINNER_INTERVAL_SECONDS,
    EscapeAction,
    EscapeState,
    ViewState,
    advance_activity,
    decision_for,
    next_mode,
    press_escape,
    reduce_event,
)
from .render import MARKUP, Panels, Styles, render_approval, render_panels

#: What a bare install is told, verbatim, instead of a traceback.
TEXTUAL_MISSING = (
    "the interactive TUI needs the 'tui' extra (Textual): pip install 'ronin[tui]'. "
    "everything else works without it: `ronin -p '…' --output-format=stream-json` for "
    "a scripted run, `python -m ronin.ui.demo` for an offline walkthrough."
)

#: What a modal popped from outside — never in normal use — is reported as. Refusing is
#: the only safe reading of a dialog that vanished without an answer.
DISMISSED = "the approval was dismissed without an answer, so the action was refused"

#: Region ids, so the tests and the CSS agree on one spelling.
MODAL_ID = "approval-modal"
TRANSCRIPT_ID = "transcript"
TOOLS_ID = "tools"
TODOS_ID = "todos"
APPROVAL_ID = "approval"
STATUS_ID = "status"
ERRORS_ID = "errors"
INPUT_ID = "prompt-input"
ACTIVITY_ID = "activity"

#: Placeholder shown in the empty input line.
INPUT_PLACEHOLDER = "type a message, Enter to send — esc interrupt, shift+tab mode, ctrl+c quit"

APP_CSS = """
Screen { layout: vertical; }
#transcript { height: 1fr; overflow-y: auto; padding: 0 1; }
#tools { height: auto; max-height: 30%; overflow-y: auto; padding: 0 1; }
#todos { height: auto; max-height: 30%; padding: 0 1; }
#approval { height: auto; padding: 0 1; }
#errors { height: auto; padding: 0 1; }
#prompt-input { dock: bottom; height: 3; margin: 0 1; }
#status { height: 1; dock: bottom; padding: 0 1; }
"""

#: The modal's own CSS. It is deliberately not a small floating box: an approval is the
#: one moment the screen has a single job, and a decision surface that shares the
#: viewport with streaming text is the class of bug that let a padded command hide its
#: own tail in another agent's UI. Taking the screen means what is shown is all there is.
MODAL_CSS = """
ApprovalModal { align: center middle; background: $background 85%; }
#approval-modal { width: 90%; height: auto; max-height: 90%; overflow-y: auto;
                  border: round $warning; padding: 1 2; }
"""


#: "Put this to the human and tell me what they said." The app hands one of these to the
#: orchestrator on mount; the orchestrator gives it to whatever the policy engine asks.
#: Typed with ``core`` values only, so the UI still knows nothing about ``safety``.
Asking = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]


def textual_available() -> bool:
    """Whether the ``tui`` extra is installed. No import, so no import cost."""
    return importlib.util.find_spec("textual") is not None


@dataclass(frozen=True, slots=True)
class Session:
    """What the app needs from the orchestrator, as injected values.

    ``events`` is an ``AsyncIterator[Event]`` — in production the loop, in a test
    or the demo a scripted list. That is the whole reason the TUI is testable with
    no model and no network.

    ``on_attach`` is the answer path, and it is what makes the app usable rather than
    merely watchable. On mount the app hands the orchestrator an :data:`Asking` — "put
    this to the human and tell me what they said" — which the orchestrator gives to
    whatever the policy engine asks. Leaving it unset keeps the old behaviour: requests
    render and nothing can be approved, which is right for the demo and for replaying a
    recording, where no live turn is waiting on an answer.

    It is deliberately *not* driven by the ``ApprovalRequest`` event, and the difference
    matters. The loop emits that event for every tool whose spec says
    ``requires_approval``, and *then* asks the policy — which in auto-accept mode allows
    the call without asking anyone. A modal driven off the event would therefore
    interrupt for every edit in the one mode whose entire purpose is not interrupting,
    and would collect an answer nobody acted on. Only the policy knows whether a human is
    needed, so the modal is raised by the policy's question and by nothing else.

    ``on_status`` supplies context-window occupancy, asked for once per ``TurnEnd``
    rather than folded from the stream. The loop reports cumulative *spend*, which is
    not the same number as how full the window is, and printing one under the other's
    label would be a lie the status line tells every second — so the orchestrator, the
    only layer that can see the live transcript, is asked instead.
    """

    events: AsyncIterator[Event]
    model: str = ""
    cwd: str = "."
    branch: str = ""
    context_used: float = 0.0
    mode: Mode = Mode.ASK
    show_thinking: bool = False
    on_interrupt: Callable[[], None] | None = None
    #: ``esc esc``: rewind to an earlier turn. Async and returning a one-line notice,
    #: because a rewind restores files (a ``git`` call) and truncates the transcript —
    #: the app runs it on a worker and shows the sentence it returns. What a rewind *is*
    #: (truncate, restore, or degrade to conversation-only) is entirely this callback's
    #: decision; the app carries the outcome and chooses nothing. Unset (demo, replay)
    #: leaves ``esc esc`` inert.
    on_rewind: Callable[[int], Awaitable[str | None]] | None = None
    on_mode_change: Callable[[Mode], None] | None = None
    on_approval: Callable[[ApprovalRequest], None] | None = None
    on_attach: Callable[[Asking], None] | None = None
    on_status: Callable[[], float] | None = None
    #: The multi-turn seam. When set, the input line is live: a submitted, non-empty
    #: message is handed here, and the orchestrator turns it into the next turn (whose
    #: events arrive on the same ``events`` iterator — see :func:`multi_turn_events`).
    #: Unset (demo, a replayed recording) leaves the input inert: nothing consumes a
    #: prompt, so there is no path from a keystroke to a turn that never runs.
    on_submit: Callable[[str], None] | None = None
    #: The clock the activity line measures silence against. Injected for the same
    #: reason every other seam here is: a test asserts what the screen says after four
    #: seconds of nothing without waiting four seconds.
    clock: Callable[[], float] = time.monotonic


def initial_state(session: Session) -> ViewState:
    """The view state a session starts from. Pure; the app calls it once."""
    return (
        ViewState()
        .with_mode(session.mode)
        .with_status(
            model=session.model,
            cwd=session.cwd,
            branch=session.branch,
            context_used=session.context_used,
        )
    )


def panels_for(state: ViewState, *, styles: Styles = MARKUP, show_thinking: bool = False) -> Panels:
    """The five regions for one state. The app's entire rendering decision."""
    return render_panels(state, styles=styles, show_thinking=show_thinking)


#: Runs one prompt's turn, yielding its events. In production this is ``agent.stream``
#: bound to its per-turn options; in a test, a function that yields a scripted list.
TurnRunner = Callable[[str], AsyncIterator[Event]]


async def multi_turn_events(
    first: str,
    submissions: asyncio.Queue[str | None],
    run_turn: TurnRunner,
) -> AsyncIterator[Event]:
    """The app's event source for a multi-turn session: run a turn, then wait for the next.

    Yields every event of the ``first`` prompt's turn, then blocks on ``submissions`` for
    the next prompt (the input line puts one there via ``Session.on_submit``) and runs it,
    repeating until ``None`` is queued. One ``Agent`` is one conversation and ``run_turn``
    continues it, so turn *n* sees the history of turns before it.

    This is the whole multi-turn orchestration, and it is pure over an injected
    ``run_turn``: tested with scripted turns and a hand-fed queue — no model, no Textual.
    Termination by a queued ``None`` is for a clean shutdown and for the tests; in a live
    session the app is torn down by ``ctrl+c``, which cancels the worker awaiting here.
    """
    prompt: str | None = first
    while prompt is not None:
        async for event in run_turn(prompt):
            yield event
        prompt = await submissions.get()


@dataclass(slots=True)
class KeyController:
    """The keyboard's state machine, with no terminal in sight.

    Mutable because a keypress *is* a state change, and it is the one place the
    app is allowed to keep any: the escape timestamp and the selected mode. Both
    transitions are pure functions from :mod:`ronin.ui.reduce`.
    """

    mode: Mode = Mode.ASK
    escape: EscapeState = field(default_factory=EscapeState)
    clock: Callable[[], float] = time.monotonic

    def press_escape(self) -> EscapeAction:
        self.escape, action = press_escape(self.escape, self.clock())
        return action

    def cycle_mode(self) -> Mode:
        self.mode = next_mode(self.mode)
        return self.mode


async def run_app(session: Session) -> int:
    """Run the interactive TUI. Returns a process exit code.

    Raises nothing on a bare install: it returns non-zero after printing
    :data:`TEXTUAL_MISSING`, because a missing optional extra is a configuration
    fact, not a crash.
    """
    if not textual_available():
        print(TEXTUAL_MISSING)
        return 1
    app = _build_app(session)
    await app.run_async()
    exit_code: object = getattr(app, "return_code", 0)
    return exit_code if isinstance(exit_code, int) else 0


def _build_app(session: Session) -> Any:
    """Construct the Textual app. Every import of Textual in Ronin is in here.

    Resolved through ``importlib`` rather than an ``import textual…`` statement so
    that the module type-checks identically whether or not the extra is installed:
    a static import would need an ``import-not-found`` ignore that becomes an
    *unused* ignore the moment someone installs Textual, and ``strict`` fails on
    both. The one remaining ignore is the ``Any`` base class. No decision is made
    inside this function.
    """
    textual_app = importlib.import_module("textual.app")
    textual_binding = importlib.import_module("textual.binding")
    textual_containers = importlib.import_module("textual.containers")
    textual_screen = importlib.import_module("textual.screen")
    textual_widgets = importlib.import_module("textual.widgets")
    app_base: Any = textual_app.App
    binding: Any = textual_binding.Binding
    vertical_scroll: Any = textual_containers.VerticalScroll
    modal_base: Any = textual_screen.ModalScreen
    static: Any = textual_widgets.Static
    input_widget: Any = textual_widgets.Input

    class ApprovalModal(modal_base):  # type: ignore[misc]  # base is Any: lazy import
        """One approval, taking the whole screen until the human answers it.

        No bindings and no buttons: every key goes through
        :func:`~ronin.ui.reduce.decision_for`, so the set of keys that can approve an
        edit is a table in a pure module and not a list of widget handlers. A key that
        means nothing is swallowed rather than treated as either answer.
        """

        CSS = MODAL_CSS

        def __init__(self, request: ApprovalRequest) -> None:
            super().__init__()
            self.request = request

        def compose(self) -> Any:
            yield static(render_approval(self.request, styles=MARKUP), id=MODAL_ID)

        def on_key(self, event: Any) -> None:
            decision = decision_for(event.key)
            if decision is None:
                return
            # Stopped *and* default-prevented: `escape` denies here, and it must not
            # also reach the app's escape binding and interrupt the turn as well.
            event.stop()
            event.prevent_default()
            self.dismiss(decision)

    class RoninApp(app_base):  # type: ignore[misc]  # base is Any: lazy import, no stubs
        CSS = APP_CSS
        BINDINGS: ClassVar[list[Any]] = [
            binding("escape", "escape", "interrupt / rewind", show=True),
            # priority: Textual's own screen-level shift+tab moves focus, and a
            # non-priority binding loses to it, so the mode key would never fire.
            binding("shift+tab", "cycle_mode", "mode", show=True, priority=True),
            binding("ctrl+c", "quit", "quit", show=True),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.session = session
            self.state = initial_state(session)
            self.keys = KeyController(mode=session.mode)
            # When the last event landed. The activity line's clock measures silence
            # from here, so "nothing has happened for 12s" is a fact rather than a guess.
            self._last_event_at = session.clock()

        def compose(self) -> Any:
            yield vertical_scroll(static("", id=TRANSCRIPT_ID), id="transcript-scroll")
            yield static("", id=TOOLS_ID)
            yield static("", id=TODOS_ID)
            yield static("", id=APPROVAL_ID)
            yield static("", id=ERRORS_ID)
            # Directly above the input, so "what is it doing" sits where the eye already
            # is between turns rather than at the far edge of the screen.
            yield static("", id=ACTIVITY_ID)
            # The multi-turn affordance. Docked above the status line so streaming text
            # fills the space between. Present even when `on_submit` is unset (demo /
            # replay); the submit handler simply has nothing to hand a prompt to then.
            yield input_widget(placeholder=INPUT_PLACEHOLDER, id=INPUT_ID)
            yield static("", id=STATUS_ID)

        def on_input_submitted(self, event: Any) -> None:
            """Enter in the prompt line: hand a non-empty message to ``on_submit``, clear.

            Textual dispatches ``Input.Submitted`` here by name. The line is cleared
            unconditionally (so trailing whitespace never lingers) but a blank submit is
            dropped — an empty prompt is not a turn. Whether a submitted prompt becomes a
            turn is the orchestrator's business, reached only through the injected
            ``on_submit``; the app itself starts nothing.
            """
            text = event.value
            event.input.value = ""
            if text.strip() and self.session.on_submit is not None:
                self.session.on_submit(text)

        def on_mount(self) -> None:
            self._paint()
            if self.session.on_attach is not None:
                self.session.on_attach(self._ask_human)
            # The only repaint not caused by an event. Without it the screen is frozen
            # between events, and a model that has been thinking for thirty seconds is
            # indistinguishable from one that has died.
            self.set_interval(SPINNER_INTERVAL_SECONDS, self._tick)
            self.run_worker(self._consume(), exclusive=True)

        def _tick(self) -> None:
            """Advance the spinner while work is in flight; do nothing when it is not.

            Gated on ``busy`` so an idle session is not repainting ten times a second
            for a line that would render empty anyway.
            """
            if not self.state.busy:
                return
            self.state = advance_activity(
                self.state,
                now=self.session.clock(),
                last_event_at=self._last_event_at,
            )
            self._paint()

        async def _ask_human(self, request: ApprovalRequest) -> ApprovalDecision:
            """Raise the modal and wait for an answer. Called by the policy's asker.

            ``push_screen_wait`` has to be awaited from a worker rather than from the
            message pump, and it is: the only caller is the policy engine, reached from
            inside ``_consume``, which *is* the worker. The pump therefore stays free to
            deliver the keypress that dismisses this — which is what makes waiting here
            safe rather than a deadlock.
            """
            self._paint()
            decision = await self.push_screen_wait(ApprovalModal(request))
            if isinstance(decision, ApprovalDecision):
                return decision
            # Dismissed without an answer — only reachable if the screen is popped from
            # outside. Refusing is the only safe reading of "no answer".
            return ApprovalDecision(approved=False, reason=DISMISSED)

        async def _consume(self) -> None:
            # Painting inside the loop, per event, is the no-buffering guarantee:
            # the first TextDelta is on screen before the second one arrives.
            async for event in self.session.events:
                # Reset the silence clock first: an event *is* progress, so the elapsed
                # figure must restart from this instant, not from the previous tick.
                self._last_event_at = self.session.clock()
                self.state = advance_activity(
                    self.state, now=self._last_event_at, last_event_at=self._last_event_at
                )
                self.state = reduce_event(self.state, event)
                if isinstance(event, ApprovalRequest) and self.session.on_approval:
                    self.session.on_approval(event)
                elif isinstance(event, TurnEnd) and self.session.on_status is not None:
                    self.state = self.state.with_status(context_used=self.session.on_status())
                self._paint()

        def _paint(self) -> None:
            panels = panels_for(self.state, show_thinking=self.session.show_thinking)
            for region, text in (
                (TRANSCRIPT_ID, panels.transcript),
                (TOOLS_ID, panels.tools),
                (TODOS_ID, panels.todos),
                (APPROVAL_ID, panels.approval),
                (ERRORS_ID, panels.errors),
                (ACTIVITY_ID, panels.activity),
                (STATUS_ID, panels.status),
            ):
                self.query_one(f"#{region}", static).update(text)

        def action_escape(self) -> None:
            action = self.keys.press_escape()
            if action is EscapeAction.REWIND:
                if self.session.on_rewind is not None:
                    # Rewind restores files (a git call) and truncates the conversation,
                    # so it is async and runs on a worker rather than blocking the key
                    # handler. Non-exclusive, so it does not cancel the event consumer.
                    self.run_worker(self._rewind(max(self.state.turn_index - 1, 0)))
            elif self.session.on_interrupt:
                self.session.on_interrupt()

        async def _rewind(self, index: int) -> None:
            """Ask the orchestrator to rewind, and surface the one-line outcome.

            The app decides nothing here: what a rewind *is* — truncate the transcript,
            restore the checkpoint, or degrade to conversation-only — is the injected
            ``on_rewind``'s call, and this only shows the sentence it returns. The
            transcript already on screen is left as the log of what happened; the notice
            is how the user learns the underlying state moved back.
            """
            if self.session.on_rewind is None:  # pragma: no cover - guarded by the caller
                return
            notice = await self.session.on_rewind(index)
            if notice:
                self.notify(notice)

        def action_cycle_mode(self) -> None:
            mode = self.keys.cycle_mode()
            self.state = self.state.with_mode(mode)
            if self.session.on_mode_change:
                self.session.on_mode_change(mode)
            self._paint()

    return RoninApp()


__all__ = [
    "ACTIVITY_ID",
    "APPROVAL_ID",
    "APP_CSS",
    "DISMISSED",
    "ERRORS_ID",
    "INPUT_ID",
    "INPUT_PLACEHOLDER",
    "MODAL_CSS",
    "MODAL_ID",
    "STATUS_ID",
    "TEXTUAL_MISSING",
    "TODOS_ID",
    "TOOLS_ID",
    "TRANSCRIPT_ID",
    "Asking",
    "KeyController",
    "Session",
    "TurnRunner",
    "initial_state",
    "multi_turn_events",
    "panels_for",
    "run_app",
    "textual_available",
]
