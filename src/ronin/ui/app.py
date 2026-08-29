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
``up``/``down`` choose an offered ``@file`` path, else walk the prompt history —
              :mod:`ronin.ui.mentions` and :func:`ronin.ui.reduce.walk_back`
``tab``       insert the chosen ``@file`` path — :func:`ronin.ui.mentions.accept`
``ctrl+c``    quit
============= ===============================================================

``@`` is the only key with a shared meaning, and the sharing is deliberate: the picker
takes the arrows only while it is open, which is a transient state directly under the
cursor, and the history has them back the moment it closes. ``esc`` is *not* a
dismissal — it interrupts the turn, which is the most important key on the screen.

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
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ronin.core.types import (
    ApprovalDecision,
    ApprovalRequest,
    Event,
    Mode,
    Todo,
    TurnEnd,
    TurnStart,
)

from .commands import is_command
from .mentions import NO_COMPLETION, Completion, accept, active_mention, rank
from .paste import NO_PASTES, PasteBook, expand, stash
from .reduce import (
    REASON_KEY,
    SPINNER_INTERVAL_SECONDS,
    EscapeAction,
    EscapeState,
    History,
    ViewState,
    advance_activity,
    decision_for,
    deny_with,
    next_mode,
    press_escape,
    reduce_event,
    remember,
    walk_back,
    walk_forward,
)
from .render import MARKUP, Panels, Styles, render_approval, render_panels

#: What a bare install is told, verbatim, instead of a traceback.
TEXTUAL_MISSING = (
    "the interactive TUI needs the 'tui' extra (Textual): pip install 'ronin[tui]'. "
    "everything else works without it: `ronin -p '…' --output-format=stream-json` for "
    "a scripted run, `python -m ronin.ui.demo` for an offline walkthrough."
)

#: Region ids, so the tests and the CSS agree on one spelling.
MODAL_ID = "approval-modal"
#: The reason line inside the approval modal. Hidden in phase one.
REASON_ID = "approval-reason"
#: What the reason line says before anything is typed. Phrased as the correction
#: itself, because the useful sentence is "use staging", not "I refuse".
REASON_PLACEHOLDER = "what should it do instead?"
TRANSCRIPT_ID = "transcript"
TOOLS_ID = "tools"
TODOS_ID = "todos"
APPROVAL_ID = "approval"
STATUS_ID = "status"
ERRORS_ID = "errors"
INPUT_ID = "prompt-input"
ACTIVITY_ID = "activity"
NOTICES_ID = "notices"
QUEUED_ID = "queued"
#: The ``@file`` picker, docked directly above the input it completes.
MENTIONS_ID = "mentions"
BANNER_ID = "banner"

#: Placeholder shown in the empty input line.
INPUT_PLACEHOLDER = "type a message, Enter to send — esc interrupt, shift+tab mode, ctrl+c quit"

APP_CSS = """
Screen { layout: vertical; }
#transcript { height: 1fr; overflow-y: auto; padding: 0 1; }
#tools { height: auto; max-height: 30%; overflow-y: auto; padding: 0 1; }
#todos { height: auto; max-height: 30%; padding: 0 1; }
#approval { height: auto; padding: 0 1; }
#errors { height: auto; padding: 0 1; }
#notices { height: auto; max-height: 40%; overflow-y: auto; padding: 0 1; }
#queued { height: auto; padding: 0 1; }
#mentions { height: auto; padding: 0 1; }
#banner { height: auto; padding: 1 1 0 1; }
#prompt-input { dock: bottom; height: 3; margin: 0 1; }
#status { height: 1; dock: bottom; padding: 0 1; }
"""

#: The modal's own CSS. It is deliberately not a small floating box: an approval is the
#: one moment the screen has a single job, and a decision surface that shares the
#: viewport with streaming text is the class of bug that let a padded command hide its
#: own tail in another agent's UI. Taking the screen means what is shown is all there is.
MODAL_CSS = """
ApprovalModal { align: center middle; background: $background 85%; }
/* Hidden *and* disabled until the reason key is pressed. Disabled is the load-bearing
   half: Textual focuses the first focusable widget when a screen mounts, so a merely
   hidden Input would still take the focus and swallow the `y` that approves. */
#approval-reason { display: none; margin: 1 2 0 2; }
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

    ``on_todos`` is the same shape for the plan, and asked on *every* event rather than
    once per turn: a checklist that only moves when the turn ends is a checklist during
    exactly the stretch where nobody needs one. The stream cannot carry it — the model's
    plan lives in ``ToolContext.todos``, written by ``todo_write`` — so again the
    orchestrator is asked.
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
    #: The model's current plan, asked for once per event so the checklist moves while
    #: the turn runs. Unset (demo, replay) leaves the panel driven by the stream alone.
    on_todos: Callable[[], Sequence[Todo]] | None = None
    #: The multi-turn seam. When set, the input line is live: a submitted, non-empty
    #: message is handed here, and the orchestrator turns it into the next turn (whose
    #: events arrive on the same ``events`` iterator — see :func:`multi_turn_events`).
    #: Unset (demo, a replayed recording) leaves the input inert: nothing consumes a
    #: prompt, so there is no path from a keystroke to a turn that never runs.
    on_submit: Callable[[str], None] | None = None
    #: The steering seam: where a message typed *while a turn is running* goes. Set, a
    #: mid-turn message joins the conversation in flight at the loop's next step instead
    #: of waiting for the whole turn to end — which is the difference between correcting
    #: the agent and correcting the transcript of what it already did wrong. Unset (demo,
    #: replay, and any consumer that has no live loop), a mid-turn message falls back to
    #: ``on_submit`` and runs as the next turn, exactly as before.
    #:
    #: Separate from ``on_submit`` rather than a flag on it, because the two land in
    #: genuinely different places — one continues the running turn, the other starts a
    #: new one — and the app must not have to know which by inspecting a return value.
    on_steer: Callable[[str], None] | None = None
    #: What is still waiting to be delivered, pulled on every event. The orchestrator
    #: owns the real queue (the loop takes from it, and the app cannot see when), so the
    #: screen has to *follow* that list rather than keep its own — the same reason
    #: ``on_status`` and ``on_todos`` are pulls. Unset leaves the display driven by the
    #: app's own bookkeeping, which is right when ``on_steer`` is unset too.
    on_steering: Callable[[], Sequence[str]] | None = None
    #: The repo's files, as repo-relative posix paths, for ``@file`` completion. Pulled
    #: only while a mention is actually being typed, never on ordinary keystrokes — the
    #: orchestrator's implementation is a cached tree walk and this is a keystroke
    #: handler. Unset leaves ``@`` as ordinary text, which is right for the demo and for
    #: a replayed recording: there is no repo behind either.
    on_files: Callable[[], Sequence[str]] | None = None
    #: Runs a slash command and returns what to show for it. Set, the input line routes
    #: anything :func:`~ronin.ui.commands.is_command` recognises here instead of to the
    #: model — which is what makes ``/help`` in the TUI run the command rather than
    #: asking the model about it. Unset (demo, replay), a slash command is just text.
    #:
    #: Async because a command may touch git or the filesystem (``/diff``, ``/undo``),
    #: and returning the output rather than printing it keeps the app a skin: the
    #: orchestrator decides what a command *does*, the app only shows the answer. A
    #: command that should become a turn (a user-defined command, a skill) is queued by
    #: the orchestrator through the same submissions queue ``on_submit`` feeds.
    on_command: Callable[[str], Awaitable[str]] | None = None
    #: The clock the activity line measures silence against. Injected for the same
    #: reason every other seam here is: a test asserts what the screen says after four
    #: seconds of nothing without waiting four seconds.
    clock: Callable[[], float] = time.monotonic
    #: The startup identity, already rendered. A string rather than a flag because the
    #: app is a skin: ``render_banner`` is a pure function the orchestrator calls, so
    #: what is on screen stays something a test can produce without a terminal. Empty
    #: means no banner, which is what the demo and a replay want.
    banner: str = ""
    #: The dialect every region is rendered through. Chosen by the caller because
    #: whether the terminal wants colour is something only the caller can know —
    #: ``render.py`` is pure and may not read the environment. ``NO_COLOUR_MARKUP``
    #: is the ``NO_COLOR`` answer: it keeps markup escaping, which is not optional
    #: for an in-band surface, and drops every colour pair.
    styles: Styles = MARKUP


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
    first: str | None,
    submissions: asyncio.Queue[str | None],
    run_turn: TurnRunner,
    *,
    leftover: Callable[[], Sequence[str]] | None = None,
) -> AsyncIterator[Event]:
    """The app's event source for a multi-turn session: run a turn, then wait for the next.

    Yields every event of the ``first`` prompt's turn, then blocks on ``submissions`` for
    the next prompt (the input line puts one there via ``Session.on_submit``) and runs it,
    repeating until ``None`` is queued. One ``Agent`` is one conversation and ``run_turn``
    continues it, so turn *n* sees the history of turns before it.

    ``leftover`` is the steering channel's safety net, drained after every turn. A
    correction typed as a turn ended — or one held back because the turn was interrupted
    — has nothing left to steer, so it becomes the next turn instead of sitting in the
    holder forever.

    This is the whole multi-turn orchestration, and it is pure over an injected
    ``run_turn``: tested with scripted turns and a hand-fed queue — no model, no Textual.
    Termination by a queued ``None`` is for a clean shutdown and for the tests; in a live
    session the app is torn down by ``ctrl+c``, which cancels the worker awaiting here.
    """
    # An empty or absent `first` means "started with no prompt": wait for the user's
    # first message rather than running a turn with nothing in it. This is what lets a
    # bare `ronin` open the view — the reason the TUI used to require an argv prompt.
    prompt: str | None = first if first else await submissions.get()
    while prompt is not None:
        async for event in run_turn(prompt):
            yield event
        # A steer can miss its turn two ways, and both end here rather than in a
        # message that is never delivered: the turn ended between the keystroke and the
        # loop's next iteration, or the turn was interrupted (the loop deliberately
        # leaves the holder untouched when it stops). Whatever is still waiting becomes
        # the next turn — which is also what "esc to stop now and send it" promises.
        if leftover is not None and (waiting := tuple(leftover())):
            # One turn, not one each: they were typed as one thought about the same
            # work, and delivering them as separate turns would let the model answer
            # the first before it could see the second.
            prompt = "\n\n".join(waiting)
            continue
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
    history: History = field(default_factory=History)
    #: The ``@file`` paths on offer. Here beside the history because both are what the
    #: arrow keys mean, and which one they mean depends on whether this is open.
    completion: Completion = NO_COMPLETION
    #: Multi-line pastes held aside for the line currently being typed. Here rather
    #: than on the app so it can be tested without a terminal, like the history above.
    pastes: PasteBook = NO_PASTES
    clock: Callable[[], float] = time.monotonic

    def press_escape(self) -> EscapeAction:
        self.escape, action = press_escape(self.escape, self.clock())
        return action

    def cycle_mode(self) -> Mode:
        self.mode = next_mode(self.mode)
        return self.mode

    def submitted(self, text: str) -> None:
        """Record a prompt that was sent, and stop browsing."""
        self.history = remember(self.history, text)
        self.completion = NO_COMPLETION

    def offer(self, text: str, cursor: int, paths: Callable[[], Sequence[str]]) -> Completion:
        """Recompute what ``@`` is offering for the token under the cursor.

        ``paths`` is a callable and is invoked *only* once the token is known to be a
        mention. That laziness is the whole reason it is not a ``Sequence``: the
        orchestrator's implementation is a cached tree walk, and calling it on every
        keystroke of ordinary prose would make every session pay for a feature it is
        not using. Passing the list in eagerly reads identically at the call site and
        quietly loses this.

        Selection resets to the top on every recomputation, deliberately: another
        character narrows the list to a *different* list, and carrying an index across
        that would leave ``tab`` inserting whatever happened to land in that slot.
        """
        mention = active_mention(text, cursor)
        if mention is None:
            self.completion = NO_COMPLETION
            return self.completion
        self.completion = Completion(candidates=rank(mention.query, paths()))
        return self.completion

    def move_completion(self, delta: int) -> Completion:
        self.completion = self.completion.moved(delta)
        return self.completion

    def take_completion(self, text: str, cursor: int) -> tuple[str, int]:
        """What ``tab`` does: the line with the mention replaced, and where to put the
        cursor. Returns the line unchanged when nothing is on offer."""
        if not self.completion.open:
            return text, cursor
        replaced = accept(text, cursor, self.completion.choice)
        self.completion = NO_COMPLETION
        return replaced

    def recall_older(self, current: str) -> str | None:
        """The previous prompt, or ``None`` if there is nothing older to show.

        ``current`` is handed in rather than read from a widget so this stays testable
        without a terminal: the caller owns the box, this owns where in history it is.
        """
        self.history, text = walk_back(self.history, current)
        return text

    def recall_newer(self) -> str | None:
        """The next prompt, ending on the user's own draft. ``None`` if not browsing."""
        self.history, text = walk_forward(self.history)
        return text


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

    class PromptInput(input_widget):  # type: ignore[misc]  # base is Any: lazy import
        """The prompt line, with a paste that keeps every line it was given.

        Textual's own handler is ``event.text.splitlines()[0]`` — a forty-line
        traceback becomes one line and the rest is dropped with no sign that anything
        happened. Here a paste of two or more lines is stashed and a marker takes its
        place; :func:`ronin.ui.paste.expand` puts the text back on submit.

        ``Paste`` is delivered straight to the focused widget rather than bubbling, and
        the base handler stops it, so this cannot be done from the app's own
        ``on_paste``. Overriding is the only seam. Textual walks the whole MRO and runs
        *every* matching handler, so suppressing the base one takes
        ``prevent_default()``; ``stop()`` alone would let it run and eat the text.
        """

        #: Set by the app after construction. A plain attribute rather than an
        #: argument because Textual owns the constructor signature.
        keys: Any = None
        on_stashed: Any = None

        def _on_paste(self, event: Any) -> None:
            if not event.text or self.keys is None:
                return
            self.keys.pastes, inserted = stash(self.keys.pastes, event.text)
            selection = self.selection
            if selection.is_empty:
                self.insert_text_at_cursor(inserted)
            else:
                self.replace(inserted, *selection)
            event.prevent_default()
            event.stop()
            if self.on_stashed is not None:
                self.on_stashed()

    class ApprovalModal(modal_base):  # type: ignore[misc]  # base is Any: lazy import
        """One approval, taking the whole screen until the human answers it.

        No bindings and no buttons: every key goes through
        :func:`~ronin.ui.reduce.decision_for`, so the set of keys that can approve an
        edit is a table in a pure module and not a list of widget handlers. A key that
        means nothing is swallowed rather than treated as either answer.

        Two phases, and the second one is why this screen has state at all.
        :data:`~ronin.ui.reduce.REASON_KEY` denies *and* asks why, which cannot be
        answered by one keystroke — so that key opens a line to type in and the screen
        waits again. The invariant across both phases: ``dismiss`` is called only with a
        complete :class:`ApprovalDecision`. There is no path that leaves the request
        answered-but-empty, because a half-resolved approval would either hang the turn
        or send the model a correction nobody wrote.
        """

        CSS = MODAL_CSS

        def __init__(self, request: ApprovalRequest) -> None:
            super().__init__()
            self.request = request
            #: Phase two. Guards every key handler, because in phase two `escape` must
            #: back out to phase one rather than deny, and `y`/`n`/`a` are just letters
            #: someone is typing into a sentence.
            self._collecting = False

        def compose(self) -> Any:
            yield static(render_approval(self.request, styles=session.styles), id=MODAL_ID)
            # `disabled` keeps it out of the focus order, which is what makes phase one
            # behave exactly as it did before this line existed: a focused Input would
            # consume the very keystrokes that answer the request.
            yield input_widget(placeholder=REASON_PLACEHOLDER, id=REASON_ID, disabled=True)

        def _repaint(self) -> None:
            body = render_approval(self.request, styles=session.styles, collecting=self._collecting)
            self.query_one(f"#{MODAL_ID}", static).update(body)

        def _begin_collecting(self) -> None:
            self._collecting = True
            line = self.query_one(f"#{REASON_ID}")
            line.disabled = False
            line.display = True
            line.value = ""
            self._repaint()
            line.focus()

        def _cancel_collecting(self) -> None:
            """Back to phase one with the request still standing, not denied.

            The one thing this must not do is resolve. Someone who pressed the reason
            key and thought better of it has not decided anything yet, and turning that
            into a refusal would punish a keystroke they took back.
            """
            self._collecting = False
            line = self.query_one(f"#{REASON_ID}")
            line.value = ""
            line.display = False
            line.disabled = True
            self._repaint()
            self.set_focus(None)

        def on_key(self, event: Any) -> None:
            if self._collecting:
                # Only `escape` is ours in phase two; every printable key belongs to the
                # input line, and `enter` arrives as `Input.Submitted` below.
                if event.key == "escape":
                    event.stop()
                    event.prevent_default()
                    self._cancel_collecting()
                return
            if event.key == REASON_KEY:
                event.stop()
                event.prevent_default()
                self._begin_collecting()
                return
            decision = decision_for(event.key)
            if decision is None:
                return
            # Stopped *and* default-prevented: `escape` denies here, and it must not
            # also reach the app's escape binding and interrupt the turn as well.
            event.stop()
            event.prevent_default()
            self.dismiss(decision)

        def on_input_submitted(self, event: Any) -> None:
            """Enter in the reason line: send the denial, with whatever was typed.

            ``deny_with`` owns the empty case — a blank reason becomes the ordinary
            denial rather than an empty correction, so pressing the reason key and then
            Enter is never worse than pressing `n`.
            """
            if not self._collecting:
                return
            event.stop()
            self.dismiss(deny_with(event.value))

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
            # Painted once and never repainted: it is identity, not state. Without it
            # the app opened on an entirely blank screen, which reads as "nothing
            # loaded" rather than "ready".
            if session.banner:
                yield static(session.banner, id=BANNER_ID)
            yield vertical_scroll(static("", id=TRANSCRIPT_ID), id="transcript-scroll")
            yield static("", id=TOOLS_ID)
            yield static("", id=TODOS_ID)
            yield static("", id=APPROVAL_ID)
            yield static("", id=ERRORS_ID)
            yield static("", id=NOTICES_ID)
            yield static("", id=QUEUED_ID)
            # Directly above the input, so "what is it doing" sits where the eye already
            # is between turns rather than at the far edge of the screen.
            yield static("", id=ACTIVITY_ID)
            # Directly above the input, so the paths on offer and the text being
            # completed are adjacent rather than at opposite ends of the screen.
            yield static("", id=MENTIONS_ID)
            # The multi-turn affordance. Docked above the status line so streaming text
            # fills the space between. Present even when `on_submit` is unset (demo /
            # replay); the submit handler simply has nothing to hand a prompt to then.
            line = PromptInput(placeholder=INPUT_PLACEHOLDER, id=INPUT_ID)
            line.keys = self.keys
            # Repaint when a paste is stashed, so the notice saying what was captured
            # appears at the moment it happens rather than on the next keystroke.
            line.on_stashed = self._paint
            yield line
            yield static("", id=STATUS_ID)

        def on_input_changed(self, event: Any) -> None:
            """Every edit of the prompt line: recompute what ``@`` is offering.

            Guarded on the widget id because the approval modal's reason line is an
            ``Input`` too, and its ``Changed`` messages bubble through here on their way
            up. Completing file paths into a denial reason would be nonsense.

            ``on_files`` is consulted only once the token under the cursor is actually a
            mention, so ordinary typing never reaches the orchestrator's tree walk.
            """
            if self.session.on_files is None or event.input.id != INPUT_ID:
                return
            before = self.keys.completion
            offered = self.keys.offer(
                event.value, event.input.cursor_position, self.session.on_files
            )
            if offered != before:
                self.state = self.state.with_completion(offered)
                self._paint()

        def on_input_submitted(self, event: Any) -> None:
            """Enter in the prompt line: hand a non-empty message to ``on_submit``, clear.

            Textual dispatches ``Input.Submitted`` here by name. The line is cleared
            unconditionally (so trailing whitespace never lingers) but a blank submit is
            dropped — an empty prompt is not a turn. Whether a submitted prompt becomes a
            turn is the orchestrator's business, reached only through the injected
            ``on_submit``; the app itself starts nothing.
            """
            # Expanded before anything else looks at it, so every path below — the
            # history, a slash command, a steer, the model — sees the text that was
            # actually pasted rather than the marker standing in for it. The book is
            # cleared with the line it belonged to; a marker recalled from history
            # after that would name a paste nobody is holding.
            text = expand(event.value, self.keys.pastes)
            self.keys.pastes = NO_PASTES
            event.input.value = ""
            if not text.strip():
                return
            # Recorded before dispatch, so a slash command is recalled too: `/model
            # sonnet` is exactly the sort of line someone retypes. Also drops any open
            # `@` picker: the line it was completing has been sent.
            self.keys.submitted(text)
            # Painted here rather than in the branches below, because only some of them
            # repaint and the picker has to come down on all of them: the line it was
            # completing has been sent. Clearing the state without painting left the
            # stale list on screen — the `Input.Changed` from emptying the line does not
            # cover it, since the state it would compare against is already clear.
            self.state = self.state.with_completion(self.keys.completion)
            self._paint()
            if is_command(text) and self.session.on_command is not None:
                # A slash command is answered locally, not sent to the model. Run it on a
                # worker: `/diff` and `/undo` shell out to git, and blocking the message
                # pump here would freeze the very screen that shows the answer.
                self.run_worker(self._run_command(text))
                return
            if self.state.busy and self.session.on_steer is not None:
                # Mid-turn: steer the running conversation rather than queueing a new
                # turn behind it. `busy` is the right test and not `pending_approval`:
                # a session parked on an approval modal is waiting on the human, and
                # there is no running turn to steer.
                self.session.on_steer(text)
                self._refresh_steering()
                self._paint()
                return
            if self.session.on_submit is not None:
                self.session.on_submit(text)
                if self.state.busy:
                    # Mid-turn with no steering seam wired (demo, replay): it will run as
                    # the next turn. Say so, rather than letting the keystroke look
                    # swallowed.
                    self.state = self.state.with_queued(text)
                    self._paint()

        def _refresh_todos(self) -> None:
            """Pull the model's plan, if the orchestrator offered a way to.

            Compared before assigning: the checklist is asked for on every event and a
            long turn is mostly events that change nothing about it, so this keeps the
            state object identical rather than rebuilding it hundreds of times per turn.
            """
            if self.session.on_todos is None:
                return
            todos = tuple(self.session.on_todos())
            if todos != self.state.todos:
                self.state = self.state.with_todos(todos)

        def _refresh_steering(self) -> None:
            """Follow the orchestrator's pending list, if it offered one.

            Pulled on every event for the same reason the checklist is: the loop takes
            corrections at its own moments, and a screen that only cleared them at
            ``TurnEnd`` would keep showing a message that was delivered two minutes ago
            as though it were still waiting.
            """
            if self.session.on_steering is None:
                return
            self.state = self.state.with_queue(self.session.on_steering())

        async def _run_command(self, text: str) -> None:
            """Run one slash command and show what it said."""
            assert self.session.on_command is not None
            output = await self.session.on_command(text)
            self.state = self.state.with_notice(output)
            self._paint()

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
            # outside. Refusing is the only safe reading of "no answer", and the refusal
            # carries no words: `reason` reaches the policy engine as the *human's own
            # words*, and nobody said anything here. A sentence invented at this layer
            # was quoted back to the model as though a person had typed it, and took the
            # branch that says "adjust the plan and continue" — the same mistake a bare
            # `n` used to make. Blank takes the engine's "do not retry it" branch.
            return ApprovalDecision(approved=False, reason="")

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
                if isinstance(event, TurnStart):
                    # A command's answer belongs to the moment it was asked; leaving it
                    # above a running turn reads as though it were part of that turn.
                    self.state = self.state.cleared_notices().cleared_queued()
                self.state = reduce_event(self.state, event)
                if isinstance(event, ApprovalRequest) and self.session.on_approval:
                    self.session.on_approval(event)
                elif isinstance(event, TurnEnd) and self.session.on_status is not None:
                    self.state = self.state.with_status(context_used=self.session.on_status())
                self._refresh_todos()
                self._refresh_steering()
                self._paint()

        def _paint(self) -> None:
            panels = panels_for(
                self.state,
                styles=self.session.styles,
                show_thinking=self.session.show_thinking,
            )
            for region, text in (
                (TRANSCRIPT_ID, panels.transcript),
                (TOOLS_ID, panels.tools),
                (TODOS_ID, panels.todos),
                (APPROVAL_ID, panels.approval),
                (ERRORS_ID, panels.errors),
                (ACTIVITY_ID, panels.activity),
                (NOTICES_ID, panels.notices),
                (QUEUED_ID, panels.queued),
                (MENTIONS_ID, panels.completion),
                (STATUS_ID, panels.status),
            ):
                self.query_one(f"#{region}", static).update(text)

        def on_key(self, event: Any) -> None:
            """``tab`` takes an offered path; ``up``/``down`` choose one, else walk history.

            Handled here rather than as an app ``BINDING`` because a priority binding
            fires ahead of the focused widget and would take these keys away from the
            approval modal's reason line. Non-priority ``on_key`` on the app sees only
            what the focused widget did not consume, and Textual's single-line ``Input``
            consumes neither arrow.

            Guarded on the prompt line actually having focus, so arrows keep meaning
            whatever they mean everywhere else — scrolling the transcript, moving inside
            some future multi-line editor — instead of being globally hijacked.

            The arrows are shared, and the ``@`` picker wins while it is open: it is
            transient and directly under the cursor, where the history is not, and it
            closes the moment the token stops being a mention. ``escape`` is
            deliberately *not* a dismissal — it interrupts the turn, which is the most
            important key on the screen, and a picker is not worth overloading it.
            ``enter`` still submits: if it took the selection instead, a message
            containing an ``@`` word could never be sent in one keystroke.
            """
            if event.key not in ("up", "down", "tab"):
                return
            line = self.query_one(f"#{INPUT_ID}", input_widget)
            if self.focused is not line:
                return
            if self.keys.completion.open:
                event.stop()
                event.prevent_default()
                if event.key == "tab":
                    line.value, cursor = self.keys.take_completion(line.value, line.cursor_position)
                    line.cursor_position = cursor
                else:
                    self.keys.move_completion(-1 if event.key == "up" else 1)
                self.state = self.state.with_completion(self.keys.completion)
                self._paint()
                return
            if event.key == "tab":
                # Nothing on offer: leave tab to whatever it means elsewhere (focus).
                return
            recalled = (
                self.keys.recall_older(line.value)
                if event.key == "up"
                else self.keys.recall_newer()
            )
            event.stop()
            event.prevent_default()
            if recalled is None:
                # Nothing older, or not browsing: hold the line as it is rather than
                # clearing it. A history key that empties the box loses work.
                return
            line.value = recalled
            # End of line, so editing a recalled prompt starts where you would type.
            line.action_end()

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
    "ERRORS_ID",
    "INPUT_ID",
    "INPUT_PLACEHOLDER",
    "MODAL_CSS",
    "MODAL_ID",
    "NOTICES_ID",
    "QUEUED_ID",
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
