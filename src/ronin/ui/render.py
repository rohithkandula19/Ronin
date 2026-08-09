"""Pure renderers: :class:`~ronin.ui.reduce.ViewState` in, ``str`` out.

Zero third-party imports and no I/O, which is what lets the same functions serve
the Textual app, the headless runner, and an HTML export. Two rules make that
possible:

- **Colour is injected.** A renderer never emits ANSI or markup of its own; it
  asks an injected :class:`Styles` map to wrap a semantic token. ``PLAIN`` (the
  default) is the identity, so every test asserts on text rather than on escape
  codes, and a new front end supplies its own map instead of a new renderer.
- **Untrusted text is escaped at the same seam.** A front end whose markup is
  in-band (Textual's ``Static`` parses ``[red]…[/red]``) would otherwise let a
  diff line containing ``[dim]`` disappear from the screen. :class:`Styles` carries
  the escape for its own markup dialect, and every renderer routes model-derived
  text through it, so the escaping cannot be forgotten in one renderer and applied
  in the others.
- **Nothing is discovered at render time.** No ``git`` subprocess for the branch,
  no ``os.getcwd()``, no clock. Every fact arrives as an argument, because a
  renderer that shells out cannot be called from a test or from a hot redraw.

Every renderer truncates deterministically and says what it cut. Silence about a
cut is the one failure mode that makes a diff or an approval prompt dangerous
rather than merely ugly.
"""
from __future__ import annotations

import difflib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from ronin.core.types import ApprovalRequest, Todo, TodoStatus

from .reduce import ToolLine, ViewState, mode_label

# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #

#: The closed set of semantic tokens a renderer may ask to have styled. Closed on
#: purpose: a typo'd token would otherwise silently render unstyled forever.
TOKENS: frozenset[str] = frozenset(
    {
        "added",
        "removed",
        "hunk",
        "meta",
        "context",
        "truncation",
        "todo_pending",
        "todo_active",
        "todo_done",
        "status",
        "danger",
        "thinking",
        "tool_ok",
        "tool_error",
        "tool_running",
    }
)


def escape_markup(text: str) -> str:
    """Neutralize console-markup metacharacters in text we did not write.

    Escaping ``[`` alone is deliberate and was checked against the installed
    Textual (8.2.5): its parser strips exactly one backslash before a ``[`` and
    leaves every other backslash alone, so doubling backslashes — what Rich's own
    ``escape`` does — puts a spurious ``\\`` on screen for a Windows path such as
    ``C:\\dir[1]``. ``tests/ui`` pins the round trip.
    """
    return text.replace("[", "\\[")


@dataclass(frozen=True, slots=True)
class Styles:
    """A token → ``(prefix, suffix)`` map, plus the escape for its own dialect.

    Absent tokens render unwrapped. ``escape`` is ``None`` for out-of-band dialects
    (plain text, ANSI), where nothing in the payload can be mistaken for a control
    sequence, and set for in-band ones (console markup).
    """

    pairs: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    escape: Callable[[str], str] | None = None

    def __post_init__(self) -> None:
        unknown = sorted(set(self.pairs) - TOKENS)
        if unknown:
            raise ValueError(
                f"unknown style tokens {unknown}; known tokens are {sorted(TOKENS)}"
            )

    def text(self, raw: str) -> str:
        """Make model-derived text safe to embed. Every renderer calls this."""
        return raw if self.escape is None else self.escape(raw)

    def wrap(self, token: str, text: str) -> str:
        if token not in TOKENS:
            raise ValueError(f"unknown style token {token!r}")
        pair = self.pairs.get(token)
        if pair is None:
            return text
        prefix, suffix = pair
        return f"{prefix}{text}{suffix}"


#: No colour. The default everywhere, and what every test asserts against.
PLAIN = Styles()

_RESET = "\x1b[0m"


def _ansi(code: str) -> tuple[str, str]:
    return (f"\x1b[{code}m", _RESET)


#: A real ANSI map for a plain terminal (the demo uses it). Not used by the
#: Textual app, which supplies markup instead.
ANSI = Styles(
    {
        "added": _ansi("32"),
        "removed": _ansi("31"),
        "hunk": _ansi("36"),
        "meta": _ansi("2"),
        "truncation": _ansi("33"),
        "todo_pending": _ansi("2"),
        "todo_active": _ansi("36"),
        "todo_done": _ansi("32"),
        "status": _ansi("2"),
        "danger": _ansi("1;31"),
        "thinking": _ansi("2;3"),
        "tool_error": _ansi("31"),
        "tool_running": _ansi("2"),
    }
)

#: Textual/Rich console markup, for the app.
MARKUP = Styles(
    escape=escape_markup,
    pairs={
        "added": ("[green]", "[/green]"),
        "removed": ("[red]", "[/red]"),
        "hunk": ("[cyan]", "[/cyan]"),
        "meta": ("[dim]", "[/dim]"),
        "truncation": ("[yellow]", "[/yellow]"),
        "todo_pending": ("[dim]", "[/dim]"),
        "todo_active": ("[cyan]", "[/cyan]"),
        "todo_done": ("[green]", "[/green]"),
        "status": ("[dim]", "[/dim]"),
        "danger": ("[bold red]", "[/bold red]"),
        "thinking": ("[dim italic]", "[/dim italic]"),
        "tool_error": ("[red]", "[/red]"),
        "tool_running": ("[dim]", "[/dim]"),
    },
)

# --------------------------------------------------------------------------- #
# Truncation
# --------------------------------------------------------------------------- #

DIFF_MAX_LINES = 400
TRANSCRIPT_MAX_LINES = 2000
TOOL_PANE_MAX_LINES = 200
TODO_MAX_ITEMS = 100
APPROVAL_MAX_LINES = 200
STATUS_MAX_WIDTH = 120


def truncate_lines(text: str, max_lines: int, *, what: str, styles: Styles = PLAIN) -> str:
    """Keep the first ``max_lines`` lines and name exactly what was dropped."""
    if max_lines <= 0:
        return text
    lines = text.split("\n")
    if len(lines) <= max_lines:
        return text
    kept = lines[:max_lines]
    marker = styles.wrap(
        "truncation",
        f"…[{what} truncated: {max_lines} of {len(lines)} lines shown]",
    )
    return "\n".join([*kept, marker])


def _elide_left(text: str, limit: int) -> str:
    """Keep the tail — for a path, the last components are the informative ones."""
    if limit <= 0 or len(text) <= limit:
        return text
    if limit == 1:
        return "…"
    return "…" + text[-(limit - 1) :]


# --------------------------------------------------------------------------- #
# Diff
# --------------------------------------------------------------------------- #

DIFF_CONTEXT = 3
#: A carriage return is shown, never passed through. A CRLF→LF change is a real
#: change, and a diff that renders it as two identical-looking lines is a lie.
CR_GLYPH = "␍"
NO_CHANGES_NOTE = "no changes"
#: Emitted when the only difference is the trailing newline, where a line-based
#: diff has nothing to show but "identical" would be wrong.
TRAILING_NEWLINE_NOTE = "\\ no line differs: only the trailing newline changed"


def _split_lines(text: str) -> tuple[list[str], bool]:
    """Split on ``\\n`` only, reporting whether the text ended with a newline.

    Deliberately not ``splitlines()``: that treats ``\\r`` as a line break and
    would erase a CRLF difference before the diff ever sees it.
    """
    if text == "":
        return [], False
    if text.endswith("\n"):
        return text[:-1].split("\n"), True
    return text.split("\n"), False


def _visible(line: str) -> str:
    return line.replace("\r", CR_GLYPH)


def render_diff(
    old: str,
    new: str,
    *,
    path: str,
    styles: Styles = PLAIN,
    context: int = DIFF_CONTEXT,
    max_lines: int = DIFF_MAX_LINES,
) -> str:
    """A real unified diff (stdlib ``difflib``) with per-line markers.

    Tabs are passed through verbatim — a tab is content, and rewriting it would
    make the rendered diff disagree with the bytes that will be written. Carriage
    returns are the exception, and they are marked (:data:`CR_GLYPH`).
    """
    old_lines, old_newline = _split_lines(old)
    new_lines, new_newline = _split_lines(new)
    raw = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=context,
            lineterm="",
        )
    )
    if not raw:
        safe_path = styles.text(path)
        header = [
            styles.wrap("meta", f"--- a/{safe_path}"),
            styles.wrap("meta", f"+++ b/{safe_path}"),
        ]
        # Equal line lists with unequal trailing newlines is the one case a
        # line-based diff cannot show; saying "no changes" there would be a lie.
        note = NO_CHANGES_NOTE if old == new else TRAILING_NEWLINE_NOTE
        return "\n".join([*header, styles.wrap("meta", note)])

    styled: list[str] = []
    for line in raw:
        safe = styles.text(_visible(line))
        if line.startswith(("---", "+++")):
            styled.append(styles.wrap("meta", safe))
        elif line.startswith("@@"):
            styled.append(styles.wrap("hunk", safe))
        elif line.startswith("+"):
            styled.append(styles.wrap("added", safe))
        elif line.startswith("-"):
            styled.append(styles.wrap("removed", safe))
        else:
            styled.append(styles.wrap("context", safe))
    if old_newline != new_newline:
        styled.append(styles.wrap("meta", TRAILING_NEWLINE_NOTE))
    return truncate_lines("\n".join(styled), max_lines, what="diff", styles=styles)


# --------------------------------------------------------------------------- #
# Todos
# --------------------------------------------------------------------------- #

TODO_GLYPHS: Mapping[TodoStatus, str] = {
    TodoStatus.PENDING: "☐",
    TodoStatus.IN_PROGRESS: "◐",
    TodoStatus.COMPLETED: "☑",
}

TODO_TOKENS: Mapping[TodoStatus, str] = {
    TodoStatus.PENDING: "todo_pending",
    TodoStatus.IN_PROGRESS: "todo_active",
    TodoStatus.COMPLETED: "todo_done",
}


def render_todos(
    todos: Sequence[Todo],
    *,
    styles: Styles = PLAIN,
    max_items: int = TODO_MAX_ITEMS,
) -> str:
    """A live checklist. Empty list renders empty, so the pane can hide itself."""
    if not todos:
        return ""
    lines = [
        styles.wrap(
            TODO_TOKENS[todo.status],
            f"{TODO_GLYPHS[todo.status]} {styles.text(todo.subject)}",
        )
        for todo in todos
    ]
    return truncate_lines("\n".join(lines), max_items, what="todo list", styles=styles)


# --------------------------------------------------------------------------- #
# Status line
# --------------------------------------------------------------------------- #

STATUS_SEPARATOR = "  ·  "
NO_BRANCH = "(no branch)"
NO_MODEL = "(no model)"
#: Shown instead of a percentage above 100: the number is wrong upstream, and
#: rendering "137% ctx" invents a plausible-looking figure.
OVER_CAPACITY = ">100%"


def render_status(
    *,
    model: str,
    context_used: float,
    cost_usd: float,
    cwd: str,
    branch: str,
    mode: str = "",
    styles: Styles = PLAIN,
    max_width: int = STATUS_MAX_WIDTH,
) -> str:
    """The persistent status line: model, context used, session cost, cwd, branch.

    ``context_used`` is a fraction (``0.42`` → ``42% ctx``). ``branch`` is passed
    in: a renderer must never shell out to git, both because it would block a
    redraw and because a test would then need a repository.
    """
    if context_used < 0.0:
        raise ValueError("context_used must be a fraction >= 0.0")
    if cost_usd < 0.0:
        raise ValueError("cost_usd must be >= 0.0")
    percent = OVER_CAPACITY if context_used > 1.0 else f"{round(context_used * 100)}%"
    segments = [
        styles.text(model) or NO_MODEL,
        f"{percent} ctx",
        f"${cost_usd:.4f}",
        styles.text(cwd),
        styles.text(branch) or NO_BRANCH,
    ]
    if mode:
        segments.insert(1, styles.text(mode))
    line = STATUS_SEPARATOR.join(segments)
    if len(line) > max_width:
        # The path is the only segment that can be shortened without losing a
        # fact, so it absorbs the overflow first.
        overflow = len(line) - max_width
        safe_cwd = styles.text(cwd)
        segments[segments.index(safe_cwd)] = _elide_left(safe_cwd, max(len(safe_cwd) - overflow, 1))
        line = STATUS_SEPARATOR.join(segments)
    if len(line) > max_width:
        line = line[: max(max_width - 1, 0)] + "…"
    return styles.wrap("status", line)


def render_status_for(state: ViewState, *, styles: Styles = PLAIN) -> str:
    """The status line for a folded state, including its selected mode."""
    return render_status(
        model=state.model,
        context_used=state.context_used,
        cost_usd=state.cost_usd,
        cwd=state.cwd,
        branch=state.branch,
        mode=mode_label(state.mode),
        styles=styles,
    )


# --------------------------------------------------------------------------- #
# Approval
# --------------------------------------------------------------------------- #

DANGER_MARKER = "⚠"
APPROVAL_PROMPT = "approve? [y]es / [n]o / [a]lways"
#: Says the human is looking at less than the whole thing. Kept loud because the
#: alternative — a quietly clipped command — is how someone approves a `rm` they
#: never saw.
APPROVAL_TRUNCATION = "the text above is incomplete; do not approve unless you accept all of it"


def render_approval(
    request: ApprovalRequest,
    *,
    styles: Styles = PLAIN,
    max_lines: int = APPROVAL_MAX_LINES,
) -> str:
    """Render exactly what the human is deciding on.

    The body is ``request.rendered``, verbatim. This function takes no arguments,
    no diff and no tool schema, so it *cannot* re-derive the command even by
    accident — that is the whole point of the field: what is shown is what will
    run. The only transformation permitted is truncation, and it announces itself
    twice (a line-count marker plus :data:`APPROVAL_TRUNCATION`).
    """
    head = styles.wrap(
        "danger",
        f"{DANGER_MARKER} {styles.text(request.name)} · {request.danger_level.name.lower()}",
    )
    parts = [head]
    if request.reason:
        parts.append(styles.wrap("meta", styles.text(request.reason)))
    body = styles.text(request.rendered)
    lines = body.split("\n")
    if len(lines) > max_lines > 0:
        body = truncate_lines(body, max_lines, what="approval text", styles=styles)
        body = f"{body}\n{styles.wrap('danger', APPROVAL_TRUNCATION)}"
    parts.append(body)
    parts.append(styles.wrap("meta", APPROVAL_PROMPT))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Transcript, tool pane, and the whole screen
# --------------------------------------------------------------------------- #

THINKING_PREFIX = "  "


def render_transcript(
    state: ViewState,
    *,
    styles: Styles = PLAIN,
    show_thinking: bool = False,
    max_lines: int = TRANSCRIPT_MAX_LINES,
) -> str:
    """The assistant's answer, reset-corrected, optionally preceded by reasoning."""
    parts: list[str] = []
    if show_thinking and state.thinking:
        reasoning = "\n".join(
            f"{THINKING_PREFIX}{styles.text(line)}" for line in state.thinking.split("\n")
        )
        parts.append(styles.wrap("thinking", reasoning))
    if state.text:
        parts.append(styles.text(state.text))
    return truncate_lines("\n".join(parts), max_lines, what="transcript", styles=styles)


def render_tool_line(line: ToolLine, *, styles: Styles = PLAIN) -> str:
    """One collapsed tool line, coloured by outcome."""
    safe = styles.text(line.text)
    if line.ok is None:
        return styles.wrap("tool_running", safe)
    return styles.wrap("tool_ok" if line.ok else "tool_error", safe)


def render_tool_lines(
    lines: Sequence[ToolLine],
    *,
    styles: Styles = PLAIN,
    max_lines: int = TOOL_PANE_MAX_LINES,
) -> str:
    if not lines:
        return ""
    rendered = "\n".join(render_tool_line(line, styles=styles) for line in lines)
    return truncate_lines(rendered, max_lines, what="tool list", styles=styles)


def render_errors(state: ViewState, *, styles: Styles = PLAIN) -> str:
    if not state.errors:
        return ""
    return "\n".join(
        styles.wrap("tool_error", styles.text(f"{error.kind}: {error.message}"))
        for error in state.errors
    )


@dataclass(frozen=True, slots=True)
class Panels:
    """Every region of the screen, already rendered.

    The app sets five widget contents from this and makes no other decision, which
    is what keeps ``app.py`` a skin: everything visible is produced by a pure
    function a test can call without a terminal.
    """

    transcript: str
    tools: str
    todos: str
    status: str
    approval: str
    errors: str


def render_panels(
    state: ViewState,
    *,
    styles: Styles = PLAIN,
    show_thinking: bool = False,
) -> Panels:
    """Render the whole screen from one folded state."""
    approval = (
        render_approval(state.pending_approval, styles=styles)
        if state.pending_approval is not None
        else ""
    )
    return Panels(
        transcript=render_transcript(state, styles=styles, show_thinking=show_thinking),
        tools=render_tool_lines(state.tool_lines, styles=styles),
        todos=render_todos(state.todos, styles=styles),
        status=render_status_for(state, styles=styles),
        approval=approval,
        errors=render_errors(state, styles=styles),
    )


__all__ = [
    "ANSI",
    "APPROVAL_MAX_LINES",
    "APPROVAL_PROMPT",
    "APPROVAL_TRUNCATION",
    "CR_GLYPH",
    "DANGER_MARKER",
    "DIFF_MAX_LINES",
    "MARKUP",
    "NO_BRANCH",
    "NO_CHANGES_NOTE",
    "OVER_CAPACITY",
    "PLAIN",
    "STATUS_SEPARATOR",
    "TODO_GLYPHS",
    "TOKENS",
    "TRAILING_NEWLINE_NOTE",
    "Panels",
    "Styles",
    "escape_markup",
    "render_approval",
    "render_diff",
    "render_errors",
    "render_panels",
    "render_status",
    "render_status_for",
    "render_todos",
    "render_tool_line",
    "render_tool_lines",
    "render_transcript",
    "truncate_lines",
]
