"""Pure renderers: :class:`~ronin.ui.reduce.ViewState` in, ``str`` out.

Zero third-party imports and no I/O, which is what lets the same functions serve
the Textual app, the headless runner, and an HTML export. Two rules make that
possible:

- **Colour is injected.** A renderer never emits ANSI or markup of its own; it
  asks an injected :class:`Styles` map to wrap a semantic token. ``PLAIN`` (the
  default) is the identity, so every test asserts on text rather than on escape
  codes, and a new front end supplies its own map instead of a new renderer.
- **Untrusted text is neutralized at the same seam.** :meth:`Styles.text` is the one
  place model-derived text passes through, so the two hazards are handled once rather
  than per renderer. Terminal control characters are stripped for every dialect — a
  terminal is an interpreter, and tool output is the most outsider-influenced text in
  the program. Markup metacharacters are escaped only where the markup is in-band:
  Textual's ``Static`` parses ``[red]…[/red]``, so a diff line containing ``[dim]``
  would otherwise disappear from the screen.
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
from typing import Final

from ronin.core.types import ApprovalRequest, Todo, TodoStatus

from .reduce import ToolLine, ViewState, activity_label, mode_label, spinner_frame

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


#: Every C0 control character except tab and newline, plus DEL and the C1 block.
#: Tab and newline are layout; everything else in these ranges is an instruction to
#: the terminal rather than something to show a person.
_CONTROLS: Final = frozenset(
    chr(code) for code in (*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0))
) - {"\n", "\t"}
_CONTROL_TABLE: Final = str.maketrans(dict.fromkeys(_CONTROLS))


def strip_controls(text: str) -> str:
    """Remove terminal control characters from text we did not write.

    A terminal is an interpreter, and model prose and tool output are the two things
    in this program that an outsider can influence — a file read out of a repository
    nobody audited, a compiler's stderr, a fetched page. Left intact, ``\\x1b]0;…\\x07``
    renames the user's window, ``\\x1b]52;c;…\\x07`` writes their clipboard in terminals
    that allow it, and ``\\x1b[2J`` or a bare ``\\r`` erases or overwrites what is
    already on screen. The last of those is the one that matters here: this program
    asks people to approve commands by reading them, and output that can paint over
    the prompt undermines the only check the user has.

    The escape *character* goes and the rest of the payload stays, so
    ``hello \\x1b]0;PWNED\\x07 world`` renders as ``hello ]0;PWNED world``: inert, and
    the attempt is still visible. Deleting the whole sequence would hide the evidence,
    which is the same reason :func:`ronin.safety.injection.wrap_untrusted` quotes an
    injection attempt rather than removing it.

    Everything, not a list of the dangerous ones. "No control characters in text we
    did not write" is a rule that can be stated in a sentence and tested exhaustively;
    "no *harmful* control characters" is a list that has to be maintained against
    every terminal feature anyone adds, and a missed entry is a silent hole. The cost
    is a compiler's colour codes in tool output, which is already truncated to a
    summary line.
    """
    return text.translate(_CONTROL_TABLE)


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

    Absent tokens render unwrapped. ``escape`` is the *markup* escape and is ``None``
    for dialects that have no markup to escape (plain text, ANSI) and set for in-band
    ones (console markup).

    Control-character stripping is separate and unconditional — see :meth:`text`. It
    used to be folded into this same ``escape`` slot, on the reasoning that an
    out-of-band dialect has nothing in its payload that could be mistaken for a
    control sequence. That is true of the *markup*, and false of the sink: a terminal
    reads ``\\x1b`` as an instruction whichever dialect wrapped the text around it.
    """

    pairs: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    escape: Callable[[str], str] | None = None

    def __post_init__(self) -> None:
        unknown = sorted(set(self.pairs) - TOKENS)
        if unknown:
            raise ValueError(f"unknown style tokens {unknown}; known tokens are {sorted(TOKENS)}")

    def text(self, raw: str) -> str:
        """Make model-derived text safe to embed. Every renderer calls this.

        Two independent hazards, in order. Control characters are stripped for every
        dialect, because the danger is the terminal on the other end rather than the
        markup on this one. Markup metacharacters are then escaped only for dialects
        that have them.

        Our own colour codes are added by :meth:`wrap` *around* the result of this
        call, so stripping here never touches them.
        """
        stripped = strip_controls(raw)
        return stripped if self.escape is None else self.escape(stripped)

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

#: Console markup with every style removed but the escaping kept. What ``NO_COLOR``
#: selects inside the Textual app.
#:
#: Not ``PLAIN``: the app renders *in band*, so a diff line reading ``[dim]`` has to
#: be escaped whether or not we colour anything, or the host's markup parser eats it.
#: Dropping the pairs without dropping ``escape`` is the difference between "no
#: colour" and "no colour and also corrupted text".
NO_COLOUR_MARKUP = Styles(escape=escape_markup)


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

#: The wordmark, in block glyphs. Thin strokes and a lot of empty space on purpose:
#: ``packages/design-system`` states the house aesthetic as sumi-e ink on warm paper,
#: "the deliberate antithesis of the neon-gradient AI aesthetic", and a heavy filled
#: banner is the thing it is the antithesis of. Every glyph here is a block or
#: box-drawing character that ruff's ambiguous-unicode rules accept, which rules out
#: the quadrant blocks and the kaomoji the v1 CLI uses.
WORDMARK: tuple[str, ...] = (
    "█▀▄ █▀█ █▄ █ █ █▄ █",
    "█▀▄ █ █ █ ▀█ █ █ ▀█",
    "▀ ▀ ▀▀▀ ▀  ▀ ▀ ▀  ▀",
)

#: Widest line of :data:`WORDMARK`, so a caller can decide before rendering.
WORDMARK_WIDTH = max(len(line) for line in WORDMARK)

#: What Ronin is, in the register the rest of the interface uses: lowercase, terse.
TAGLINE = "masterless · terminal-native"

#: The compact form, for a terminal too narrow for the wordmark.
COMPACT_WORDMARK = "ronin"

#: Indent the wordmark sits at, matching the two-space gutter every other region uses.
BANNER_INDENT = "  "


def render_banner(
    *,
    version: str = "",
    hint: str = "",
    width: int = 0,
    styles: Styles = PLAIN,
) -> str:
    """The startup identity: wordmark, what this is, and how to get help.

    Pure like every other renderer — the version and the hint arrive as arguments
    rather than being read from ``importlib.metadata`` or a command registry here,
    for the reason stated at the top of this module: a renderer that discovers things
    cannot be called from a test or from a redraw.

    ``width`` of 0 means "no constraint". A terminal narrower than the wordmark gets
    the compact form rather than a wrapped one, because a wordmark that wraps is
    worse than no wordmark.
    """
    subtitle = TAGLINE if not version else f"{TAGLINE} · {version}"
    lines: list[str] = []
    if width and width < len(BANNER_INDENT) + WORDMARK_WIDTH:
        lines.append(f"{BANNER_INDENT}{COMPACT_WORDMARK} {styles.wrap('meta', subtitle)}")
    else:
        lines.extend(f"{BANNER_INDENT}{line}" for line in WORDMARK)
        lines.append("")
        lines.append(f"{BANNER_INDENT}{styles.wrap('meta', subtitle)}")
    if hint:
        lines.append(f"{BANNER_INDENT}{styles.wrap('meta', hint)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Truncation
# --------------------------------------------------------------------------- #

DIFF_MAX_LINES = 400
TRANSCRIPT_MAX_LINES = 2000
TOOL_PANE_MAX_LINES = 200
TODO_MAX_ITEMS = 100
APPROVAL_MAX_LINES = 200
#: `/help` is a table and a command may print a diff; generous, but still bounded.
NOTICE_MAX_LINES = 300
STATUS_MAX_WIDTH = 120

#: Marks a streamed output line as belonging to the tool line above it.
TOOL_OUTPUT_INDENT = "  │ "

#: Between the activity label and its clock. Narrower than the status line's separator:
#: this is one line's two fields, not a row of independent facts.
ACTIVITY_SEPARATOR = " · "

#: When the context gauge starts warning. Just under the 0.8 at which compaction fires, so
#: the warning arrives *before* the fold rather than reporting it afterwards.
CONTEXT_WARN_FRACTION = 0.75
#: Appended to the context percentage once past the warning line.
CONTEXT_WARN_MARK = " ⚠"

#: What the queued-message line says. Names the timing, because "queued" alone does not
#: tell the user whether to wait or to interrupt.
QUEUED_ONE = "queued — runs when this turn ends (esc to interrupt now)"
QUEUED_MANY = "{n} queued — they run in order when this turn ends (esc to interrupt now)"
QUEUED_INDENT = "  | "

#: How long in-flight work must go quiet before the activity line shows a clock. Below
#: this every ordinary tool would flash a number; above it, the number appearing is the
#: warning that something is slow.
ACTIVITY_ELAPSED_AFTER_SECONDS = 2.0


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
    if context_used >= CONTEXT_WARN_FRACTION:
        # A gauge that only reports is no use at the one moment it matters. Compaction
        # fires silently at 80%, so the number crossing this line is the user's only
        # notice that the window is about to be folded under them.
        percent = f"{percent}{CONTEXT_WARN_MARK}"
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


def render_tool_output(line: ToolLine, *, styles: Styles = PLAIN) -> str:
    """The expansion under a running tool: the tail of what it has printed so far.

    Indented and dimmed so it reads as subordinate to its tool line rather than as
    transcript. Returns ``""`` for a tool that has streamed nothing, which keeps the
    pane exactly as it was for every tool that does not stream.
    """
    tail = [item for item in line.output_tail]
    while tail and not tail[-1]:
        # A chunk ending in a newline leaves an empty final element. It is a real part
        # of the buffer (the next chunk continues from it) but a blank row on screen.
        tail.pop()
    if not tail:
        return ""
    return "\n".join(
        styles.wrap("meta", styles.text(f"{TOOL_OUTPUT_INDENT}{item}")) for item in tail
    )


def render_tool_lines(
    lines: Sequence[ToolLine],
    *,
    styles: Styles = PLAIN,
    max_lines: int = TOOL_PANE_MAX_LINES,
) -> str:
    if not lines:
        return ""
    blocks: list[str] = []
    for line in lines:
        blocks.append(render_tool_line(line, styles=styles))
        expansion = render_tool_output(line, styles=styles)
        if expansion:
            blocks.append(expansion)
    return truncate_lines("\n".join(blocks), max_lines, what="tool list", styles=styles)


def render_activity(state: ViewState, *, styles: Styles = PLAIN) -> str:
    """The live "what is happening" line: spinner, what is running, and how long.

    Empty whenever nothing is in flight, so a settled screen carries no residue of the
    last turn. The elapsed clock appears only after
    :data:`ACTIVITY_ELAPSED_AFTER_SECONDS`: a number that flickers on for every fast
    tool is noise, while a number that *appears* is itself the signal that something is
    taking longer than it should.
    """
    label = activity_label(state)
    if not label:
        return ""
    head = " ".join(part for part in (spinner_frame(state.tick), styles.text(label)) if part)
    if state.waiting_seconds >= ACTIVITY_ELAPSED_AFTER_SECONDS:
        head = f"{head}{ACTIVITY_SEPARATOR}{int(state.waiting_seconds)}s"
    return styles.wrap("tool_running", head)


def render_queued(state: ViewState, *, styles: Styles = PLAIN) -> str:
    """What the user typed while the agent was working, and what will happen to it.

    Shown because the alternative is what the session audit found: a correction typed
    mid-turn silently waits with nothing on screen acknowledging it, which reads as the
    keystroke having been swallowed. Saying *when* it will run is the point — the user
    decides whether to also press esc.
    """
    if not state.queued:
        return ""
    head = QUEUED_ONE if len(state.queued) == 1 else QUEUED_MANY.format(n=len(state.queued))
    lines = [styles.wrap("meta", head)]
    lines += [styles.wrap("meta", styles.text(f"{QUEUED_INDENT}{item}")) for item in state.queued]
    return "\n".join(lines)


def render_notices(state: ViewState, *, styles: Styles = PLAIN) -> str:
    """Local answers — a slash command's output, a rewind's outcome.

    Deliberately a region of its own rather than part of the transcript. The transcript
    is what the *model* said; `/help`'s table folded into it would make the conversation
    a record of two voices with no way to tell them apart, and an exported session would
    carry the confusion forward.
    """
    if not state.notices:
        return ""
    body = "\n".join(styles.text(notice) for notice in state.notices)
    return truncate_lines(body, NOTICE_MAX_LINES, what="command output", styles=styles)


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
    #: The live spinner line. Empty when nothing is in flight — the app clears the
    #: region rather than leaving the last turn's activity on screen.
    activity: str = ""
    #: Local answers: slash-command output and other things the session said itself.
    notices: str = ""
    #: Messages typed mid-turn and waiting their turn. Empty when nothing is queued.
    queued: str = ""


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
        activity=render_activity(state, styles=styles),
        notices=render_notices(state, styles=styles),
        queued=render_queued(state, styles=styles),
    )


__all__ = [
    "ACTIVITY_ELAPSED_AFTER_SECONDS",
    "ANSI",
    "APPROVAL_MAX_LINES",
    "APPROVAL_PROMPT",
    "APPROVAL_TRUNCATION",
    "CONTEXT_WARN_FRACTION",
    "CONTEXT_WARN_MARK",
    "CR_GLYPH",
    "DANGER_MARKER",
    "DIFF_MAX_LINES",
    "MARKUP",
    "NOTICE_MAX_LINES",
    "NO_BRANCH",
    "NO_CHANGES_NOTE",
    "OVER_CAPACITY",
    "PLAIN",
    "QUEUED_INDENT",
    "QUEUED_MANY",
    "QUEUED_ONE",
    "STATUS_SEPARATOR",
    "TODO_GLYPHS",
    "TOKENS",
    "TOOL_OUTPUT_INDENT",
    "TRAILING_NEWLINE_NOTE",
    "Panels",
    "Styles",
    "escape_markup",
    "render_activity",
    "render_approval",
    "render_diff",
    "render_errors",
    "render_notices",
    "render_panels",
    "render_queued",
    "render_status",
    "render_status_for",
    "render_todos",
    "render_tool_line",
    "render_tool_lines",
    "render_tool_output",
    "render_transcript",
    "strip_controls",
    "truncate_lines",
]
