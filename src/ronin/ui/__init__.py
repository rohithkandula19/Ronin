"""The interface layer: one event stream, three consumers.

``ronin.ui`` depends on ``ronin.core`` and nothing else — not providers, not
tools, not the session. Every surface here consumes an ``AsyncIterator[Event]``
that somebody else produced, which is what makes the TUI testable with no model
and no network: in ``tests/ui`` and in ``ronin.ui.demo`` that iterator is a
scripted list.

- :mod:`ronin.ui.reduce` folds events into a frozen ``ViewState`` (pure, stdlib).
- :mod:`ronin.ui.render` turns a ``ViewState`` into strings (pure, stdlib).
- :mod:`ronin.ui.commands` parses and validates slash commands (pure, stdlib).
- :mod:`ronin.ui.headless` is the ``-p`` / JSON-lines path (stdlib).
- :mod:`ronin.ui.app` is the Textual app, and imports Textual lazily so the four
  modules above work on a bare install.
"""

from __future__ import annotations

from .app import KeyController, Session, run_app, textual_available
from .commands import (
    BUILTIN_COMMANDS,
    BUILTIN_REGISTRY,
    ArgSpec,
    Command,
    ParsedCommand,
    ParseError,
    Registry,
    UserCommand,
    is_command,
    load_registry,
    load_user_commands,
    parse,
    render_help,
    substitute,
    suggest,
)
from .headless import (
    EXIT_ERROR,
    EXIT_NEEDS_APPROVAL,
    EXIT_OK,
    SCHEMA,
    HeadlessPolicy,
    HeadlessResult,
    OutputFormat,
    event_to_json,
    exit_code_for,
    run_headless,
)
from .reduce import (
    APPROVAL_KEYS,
    DOUBLE_ESCAPE_WINDOW_SECONDS,
    MODE_CYCLE,
    EscapeAction,
    EscapeState,
    ToolLine,
    ViewState,
    decision_for,
    mode_label,
    next_mode,
    press_escape,
    reduce_all,
    reduce_event,
    reduce_stream,
)
from .render import (
    ANSI,
    MARKUP,
    PLAIN,
    Panels,
    Styles,
    render_approval,
    render_diff,
    render_panels,
    render_status,
    render_todos,
    render_tool_lines,
    render_transcript,
    strip_controls,
)

__all__ = [
    "ANSI",
    "APPROVAL_KEYS",
    "BUILTIN_COMMANDS",
    "BUILTIN_REGISTRY",
    "DOUBLE_ESCAPE_WINDOW_SECONDS",
    "EXIT_ERROR",
    "EXIT_NEEDS_APPROVAL",
    "EXIT_OK",
    "MARKUP",
    "MODE_CYCLE",
    "PLAIN",
    "SCHEMA",
    "ArgSpec",
    "Command",
    "EscapeAction",
    "EscapeState",
    "HeadlessPolicy",
    "HeadlessResult",
    "KeyController",
    "OutputFormat",
    "Panels",
    "ParseError",
    "ParsedCommand",
    "Registry",
    "Session",
    "Styles",
    "ToolLine",
    "UserCommand",
    "ViewState",
    "decision_for",
    "event_to_json",
    "exit_code_for",
    "is_command",
    "load_registry",
    "load_user_commands",
    "mode_label",
    "next_mode",
    "parse",
    "press_escape",
    "reduce_all",
    "reduce_event",
    "reduce_stream",
    "render_approval",
    "render_diff",
    "render_help",
    "render_panels",
    "render_status",
    "render_todos",
    "render_tool_lines",
    "render_transcript",
    "run_app",
    "run_headless",
    "strip_controls",
    "substitute",
    "suggest",
    "textual_available",
]
