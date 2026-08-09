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

The app never approves anything by itself. It renders
:class:`~ronin.core.types.ApprovalRequest` verbatim and calls the
``on_approval`` callback the caller injected; a UI that answered on its own
behalf would be exactly the auto-approval this codebase refuses.
"""
from __future__ import annotations

import importlib.util
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ronin.core.types import ApprovalRequest, Event, Mode

from .reduce import (
    EscapeAction,
    EscapeState,
    ViewState,
    next_mode,
    press_escape,
    reduce_event,
)
from .render import MARKUP, Panels, Styles, render_panels

#: What a bare install is told, verbatim, instead of a traceback.
TEXTUAL_MISSING = (
    "the interactive TUI needs the 'tui' extra (Textual): pip install 'ronin[tui]'. "
    "everything else works without it: `ronin -p '…' --output-format=stream-json` for "
    "a scripted run, `python -m ronin.ui.demo` for an offline walkthrough."
)

#: Region ids, so the tests and the CSS agree on one spelling.
TRANSCRIPT_ID = "transcript"
TOOLS_ID = "tools"
TODOS_ID = "todos"
APPROVAL_ID = "approval"
STATUS_ID = "status"
ERRORS_ID = "errors"

APP_CSS = """
Screen { layout: vertical; }
#transcript { height: 1fr; overflow-y: auto; padding: 0 1; }
#tools { height: auto; max-height: 30%; overflow-y: auto; padding: 0 1; }
#todos { height: auto; max-height: 30%; padding: 0 1; }
#approval { height: auto; padding: 0 1; }
#errors { height: auto; padding: 0 1; }
#status { height: 1; dock: bottom; padding: 0 1; }
"""


def textual_available() -> bool:
    """Whether the ``tui`` extra is installed. No import, so no import cost."""
    return importlib.util.find_spec("textual") is not None


@dataclass(frozen=True, slots=True)
class Session:
    """What the app needs from the orchestrator, as injected values.

    ``events`` is an ``AsyncIterator[Event]`` — in production the loop, in a test
    or the demo a scripted list. That is the whole reason the TUI is testable with
    no model and no network.
    """

    events: AsyncIterator[Event]
    model: str = ""
    cwd: str = "."
    branch: str = ""
    context_used: float = 0.0
    mode: Mode = Mode.ASK
    show_thinking: bool = False
    on_interrupt: Callable[[], None] | None = None
    on_rewind: Callable[[int], None] | None = None
    on_mode_change: Callable[[Mode], None] | None = None
    on_approval: Callable[[ApprovalRequest], None] | None = None


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


def panels_for(
    state: ViewState, *, styles: Styles = MARKUP, show_thinking: bool = False
) -> Panels:
    """The five regions for one state. The app's entire rendering decision."""
    return render_panels(state, styles=styles, show_thinking=show_thinking)


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
    textual_widgets = importlib.import_module("textual.widgets")
    app_base: Any = textual_app.App
    binding: Any = textual_binding.Binding
    vertical_scroll: Any = textual_containers.VerticalScroll
    static: Any = textual_widgets.Static

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

        def compose(self) -> Any:
            yield vertical_scroll(static("", id=TRANSCRIPT_ID), id="transcript-scroll")
            yield static("", id=TOOLS_ID)
            yield static("", id=TODOS_ID)
            yield static("", id=APPROVAL_ID)
            yield static("", id=ERRORS_ID)
            yield static("", id=STATUS_ID)

        def on_mount(self) -> None:
            self._paint()
            self.run_worker(self._consume(), exclusive=True)

        async def _consume(self) -> None:
            # Painting inside the loop, per event, is the no-buffering guarantee:
            # the first TextDelta is on screen before the second one arrives.
            async for event in self.session.events:
                self.state = reduce_event(self.state, event)
                if isinstance(event, ApprovalRequest) and self.session.on_approval:
                    self.session.on_approval(event)
                self._paint()

        def _paint(self) -> None:
            panels = panels_for(
                self.state, show_thinking=self.session.show_thinking
            )
            for region, text in (
                (TRANSCRIPT_ID, panels.transcript),
                (TOOLS_ID, panels.tools),
                (TODOS_ID, panels.todos),
                (APPROVAL_ID, panels.approval),
                (ERRORS_ID, panels.errors),
                (STATUS_ID, panels.status),
            ):
                self.query_one(f"#{region}", static).update(text)

        def action_escape(self) -> None:
            action = self.keys.press_escape()
            if action is EscapeAction.REWIND:
                if self.session.on_rewind:
                    self.session.on_rewind(max(self.state.turn_index - 1, 0))
            elif self.session.on_interrupt:
                self.session.on_interrupt()

        def action_cycle_mode(self) -> None:
            mode = self.keys.cycle_mode()
            self.state = self.state.with_mode(mode)
            if self.session.on_mode_change:
                self.session.on_mode_change(mode)
            self._paint()

    return RoninApp()


__all__ = [
    "APPROVAL_ID",
    "APP_CSS",
    "ERRORS_ID",
    "STATUS_ID",
    "TEXTUAL_MISSING",
    "TODOS_ID",
    "TOOLS_ID",
    "TRANSCRIPT_ID",
    "KeyController",
    "Session",
    "initial_state",
    "panels_for",
    "run_app",
    "textual_available",
]
