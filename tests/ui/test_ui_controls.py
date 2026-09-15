"""Terminal control characters in text we did not write.

A terminal is an interpreter, and the two most outsider-influenced strings in this
program both end up on one: model prose, and tool output — a file read out of a
repository nobody audited, a compiler's stderr, a page that was fetched. Left intact,
`\\x1b]0;…\\x07` renames the user's window, `\\x1b]52;c;…\\x07` writes their clipboard in
terminals that allow it, and `\\x1b[2J`, `\\x1b[2K` or a bare `\\r` erase or overwrite what
is already on screen.

The last of those is why this is a safety property rather than a cosmetic one. This
program asks people to approve destructive commands by *reading* them. A `rendered`
string that can move the cursor can paint over the command the user is agreeing to, so
what they read and what they approve stop being the same thing — and the approval gate is
the only check the user has.

`render.py` already had the right principle for its other hazard: "untrusted text is
neutralized at the same seam", implemented for Textual's `[` markup. Control characters
were not covered, and `PLAIN`/`ANSI` set `escape=None` on the reasoning that an
out-of-band dialect has nothing in its payload that could be mistaken for a control
sequence. True of the markup; false of the sink.

The rule is *every* control character rather than the dangerous ones. "No control
characters in text we did not write" can be stated in a sentence and tested exhaustively,
which is what the first test here does; "no harmful control characters" is a list that
has to be maintained against every terminal feature anyone adds.
"""

from __future__ import annotations

import asyncio

import pytest

from ronin.core.types import (
    ApprovalRequest,
    DangerLevel,
    Error,
    TextDelta,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.ui.headless import OutputFormat, run_headless
from ronin.ui.render import (
    ANSI,
    MARKUP,
    NO_COLOUR_MARKUP,
    PLAIN,
    Styles,
    render_approval,
    strip_controls,
)

#: The three attacks named in `strip_controls`, in one string.
HOSTILE = "hello \x1b]0;PWNED\x07 \x1b[2J \x1b]52;c;cHduZWQ=\x07 world"

#: Every dialect. The point of the change is that this property is not per-dialect.
#: A new dialect belongs here the day it is added, which is the whole reason the
#: control-stripping tests are parametrized rather than written three times.
DIALECTS: tuple[tuple[str, Styles], ...] = (
    ("PLAIN", PLAIN),
    ("ANSI", ANSI),
    ("MARKUP", MARKUP),
    ("NO_COLOUR_MARKUP", NO_COLOUR_MARKUP),
)


# --------------------------------------------------------------------------- #
# what goes and what stays
# --------------------------------------------------------------------------- #


def test_every_control_character_is_removed_and_nothing_else_is() -> None:
    """Exhaustive over the ranges, which is the whole argument for the broad rule:
    the claim is checkable in one loop rather than trusted."""
    for code in (*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0)):
        char = chr(code)
        result = strip_controls(f"a{char}b")
        if char in {"\n", "\t"}:
            assert result == f"a{char}b", f"U+{code:04X} is layout and must survive"
        else:
            assert result == "ab", f"U+{code:04X} survived"


def test_tab_and_newline_survive_because_they_are_layout() -> None:
    """Stripping these would run a diff into one line and destroy the indentation of
    every code block the model prints — the output would be safe and useless."""
    assert strip_controls("def f():\n\treturn 1\n") == "def f():\n\treturn 1\n"


def test_ordinary_text_is_returned_unchanged() -> None:
    """Byte-identical for the overwhelmingly common case, including non-ASCII: a
    normalizer that touched ordinary prose would show up in every diff and every
    approval prompt."""
    for text in ("hello world", "héllo — ✓ 日本語", "C:\\dir[1]", "", "a" * 10_000):
        assert strip_controls(text) == text


def test_the_escape_character_goes_and_the_payload_stays_visible() -> None:
    """Removing the whole sequence would hide the attempt. The user is better served
    seeing `]0;PWNED` sitting in their output than seeing nothing and wondering why
    their window is called something new — the same reason `wrap_untrusted` quotes an
    injection rather than deleting it."""
    assert strip_controls(HOSTILE) == "hello ]0;PWNED \x5b2J ]52;c;cHduZWQ= world".replace(
        "\x5b2J", "[2J"
    )


def test_a_carriage_return_cannot_overwrite_the_line() -> None:
    """`\\r` needs no escape character at all, which makes it the easiest of these to
    forget: `"real answer\\rfake answer"` shows only the fake one on a terminal."""
    assert strip_controls("real answer\rfake answer") == "real answerfake answer"


# --------------------------------------------------------------------------- #
# the seam: every dialect, and our own colour is not collateral
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("name", "styles"), DIALECTS, ids=[name for name, _ in DIALECTS])
def test_every_dialect_strips_controls_at_the_text_seam(name: str, styles: Styles) -> None:
    """Including the two whose `escape` is `None`. That was the bug: the escape slot
    carried both concerns, so a dialect with no *markup* to escape got no control
    handling either."""
    assert "\x1b" not in styles.text(HOSTILE), f"{name} passed an escape through"
    assert "\x07" not in styles.text(HOSTILE)


def test_the_markup_dialect_still_escapes_its_own_metacharacters() -> None:
    """Both hazards, not one instead of the other: a diff line containing `[dim]` must
    still be escaped, or Textual parses it as markup and the line vanishes."""
    assert MARKUP.text("[dim]") == "\\[dim]"
    assert MARKUP.text("\x1b[dim]") == "\\[dim]", "control stripped, markup still escaped"


def test_our_own_ansi_colour_survives_because_wrap_runs_after_text() -> None:
    """The obvious way to implement this — strip on the way out — would delete the
    colour codes the renderer just added. `text()` neutralizes the payload and `wrap()`
    puts our own sequences around the result, so the order does the work."""
    coloured = ANSI.wrap("added", ANSI.text("+ added a line"))
    assert coloured.startswith("\x1b[32m")
    assert coloured.endswith("\x1b[0m")
    assert "added a line" in coloured


def test_a_payload_that_looks_like_our_own_colour_code_is_still_neutralized() -> None:
    """A model that emits `\\x1b[32m` gets no colour, because the escape is gone before
    `wrap` ever runs. Colour in this program is a fact about the token, never about what
    the text asked for."""
    assert ANSI.text("\x1b[32mfake green\x1b[0m") == "[32mfake green[0m"


# --------------------------------------------------------------------------- #
# the approval prompt, which is the reason this matters
# --------------------------------------------------------------------------- #


def test_an_approval_line_cannot_paint_over_the_command_it_shows() -> None:
    """The attack this closes, spelled out.

    `\\x1b[2K\\x1b[A` erases the line and moves the cursor up, so a terminal shows the
    text that follows *in place of* the text before it. A `rendered` string built that
    way would display `ls -la` while the command awaiting approval is `rm -rf /`. After
    stripping, both halves are visible and the user can see they are being played.
    """
    spoofed = "rm -rf /\x1b[2K\x1b[Als -la"
    shown = PLAIN.text(spoofed)

    assert "rm -rf /" in shown, "the real command must still be on screen"
    assert "\x1b" not in shown
    assert shown == "rm -rf /[2K[Als -la"


# --------------------------------------------------------------------------- #
# the two paths that reach a terminal without a Styles map
# --------------------------------------------------------------------------- #


async def hostile_stream() -> object:
    yield TurnStart(turn_index=0)
    yield TextDelta(text=HOSTILE)
    yield ToolStart(tool_use_id="t1", name="read", arguments={"path": "a\x1b]0;X\x07.py"})
    yield ToolEnd(
        tool_use_id="t1",
        name="read",
        result=ToolResult(ok=True, content="file says \x1b]0;TOOL\x07 done"),
    )
    yield Error(message="boom \x1b[2J", kind="tool", recoverable=True)
    yield TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="done")


async def test_the_headless_text_format_strips_before_writing() -> None:
    """`--output-format text` writes the answer straight to stdout with no `Styles` in
    the path, so it needs the same treatment a renderer gives it."""
    written: list[str] = []
    await run_headless(
        hostile_stream(),  # type: ignore[arg-type]
        output_format=OutputFormat.TEXT,
        write=written.append,
        write_error=written.append,
    )
    assert "\x1b" not in "".join(written)
    assert "world" in "".join(written), "the answer itself must still arrive"


async def test_the_json_formats_were_already_safe_and_stay_that_way() -> None:
    """No change was needed here and none was made: JSON encoding turns an escape into
    a six-character `\\u001b` that is inert by the time anything prints it. Asserted so
    a future "strip everywhere" refactor cannot quietly corrupt the machine-readable
    stream, which a CI consumer parses."""
    written: list[str] = []
    await run_headless(
        hostile_stream(),  # type: ignore[arg-type]
        output_format=OutputFormat.STREAM_JSON,
        write=written.append,
        write_error=written.append,
    )
    joined = "".join(written)
    assert "\x1b" not in joined
    assert "u001b" in joined, "the escape should survive as data in the JSON stream"


def test_the_line_session_strips_every_string_it_prints() -> None:
    """`cli.main._print_event` is the interactive path and builds its own lines, so it
    does not inherit the seam. Five event kinds, five model- or tool-derived strings."""
    from ronin.cli.main import Streams, _print_event

    out: list[str] = []
    err: list[str] = []
    streams = Streams(out=out.append, err=err.append, ask=lambda _q: "", flush=lambda: None)

    for event in (
        TextDelta(text=HOSTILE),
        ToolStart(tool_use_id="t", name="read", arguments={"path": "a\x1b]0;X\x07.py"}),
        ToolEnd(tool_use_id="t", name="read", result=ToolResult(ok=True, content="x\x1b]0;T\x07")),
        ApprovalRequest(
            tool_use_id="t",
            name="bash",
            danger_level=DangerLevel.DESTRUCTIVE,
            rendered="rm -rf /\x1b[2K\x1b[Als -la",
            reason="destructive",
        ),
        Error(message="boom \x1b[2J", kind="tool", recoverable=True),
    ):
        _print_event(event, streams)

    printed = "".join(out) + "".join(err)
    assert "\x1b" not in printed, printed
    assert "rm -rf /" in printed, "the command under approval must still be shown"


def test_the_stripper_is_reachable_from_the_package_root() -> None:
    """Exported because `cli` needs it: the line session prints to a terminal without a
    `Styles` map, and an unexported helper would have been reimplemented there."""
    from ronin.ui import strip_controls as exported

    assert exported is strip_controls


# --------------------------------------------------------------------------- #
# the characters that reorder what is left
# --------------------------------------------------------------------------- #
#
# The loop above is exhaustive over C0, DEL and C1 — and stops at U+009F. Every
# character below is past that, which is how a test that called itself exhaustive
# passed while an approval prompt could be shown to a human backwards.
#
# The harm is different from an escape sequence's. Nothing is painted over; the line
# is *reordered*, so `rm -rf ~ #<RLO>...` shows the comment first and the destructive
# half behind it, and the person approves what they read rather than what runs. The
# same family makes two different strings render identically, so a diff can show one
# identifier while containing another.

RLO = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE
LRI = chr(0x2066)  # LEFT-TO-RIGHT ISOLATE
PDI = chr(0x2069)  # POP DIRECTIONAL ISOLATE
ZWSP = chr(0x200B)  # ZERO WIDTH SPACE
ZWJ = chr(0x200D)  # ZERO WIDTH JOINER — kept on purpose, see below

#: Everything that must be made visible, as (codepoint, why it is here).
REORDERING = (
    0x061C,
    0x180E,
    0x200B,
    0x200E,
    0x200F,
    *range(0x202A, 0x202F),
    *range(0x2060, 0x2065),
    *range(0x2066, 0x206A),
    0xFEFF,
    0xE0001,
    0xE0041,
    0xE007F,
)


@pytest.mark.parametrize("code", REORDERING)
def test_every_invisible_character_is_made_visible(code: int) -> None:
    """Rendered, not deleted — deleting leaves the doctored line looking honest."""
    assert strip_controls(f"a{chr(code)}b") == f"a<U+{code:04X}>b"


def test_a_reordered_command_reads_in_execution_order_once_rendered() -> None:
    """The attack, end to end: what the reader sees now matches what the shell runs."""
    doctored = f"rm -rf ~ #{RLO}{LRI} ecaps ksid kcehc #{PDI}"
    shown = strip_controls(doctored)
    assert "rm -rf ~" in shown
    assert RLO not in shown and LRI not in shown and PDI not in shown
    assert "<U+202E>" in shown


def test_an_invisible_character_in_an_identifier_is_visible() -> None:
    """`from o<ZWSP>s import system` renders identically to the honest line otherwise."""
    assert strip_controls(f"from o{ZWSP}s import system") == "from o<U+200B>s import system"


def test_the_zero_width_joiner_survives_because_scripts_and_emoji_need_it() -> None:
    """The deliberate exception, pinned so it is not "fixed" by someone tidying up.

    ZWNJ and ZWJ are required to render Persian, Hindi and emoji sequences, they
    cannot reorder anything, and an identifier containing one is a syntax error in
    every language this program edits. Marking them would corrupt legitimate text to
    defend against nothing.
    """
    assert strip_controls(f"a{ZWJ}b") == f"a{ZWJ}b"
    assert strip_controls(f"a{chr(0x200C)}b") == f"a{chr(0x200C)}b"


def test_ordinary_text_is_untouched() -> None:
    """The cost of the rule has to stay zero for the text people actually read."""
    source = "def main() -> None:\n\treturn 1  # ok\n"
    assert strip_controls(source) == source


def test_the_two_families_compose() -> None:
    """An escape sequence and a bidi control in one string, each handled its own way."""
    shown = strip_controls(f"hi \x1b]0;X\x07 {RLO}there")
    # The escape character goes and its payload stays inert; the bidi control is named.
    assert shown == "hi ]0;X <U+202E>there"


def test_an_approval_prompt_carries_none_of_them_through() -> None:
    """The seam that matters: every renderer reaches the terminal through this.

    Asserted on `render_approval` rather than on `strip_controls` because the bug was
    never in the stripper alone — it was that the stripper's idea of "control
    character" stopped before the characters that could reorder an approval.
    """
    doctored = f"rm -rf ~ #{RLO}{LRI} ecaps ksid kcehc #{PDI}"
    request = ApprovalRequest(
        tool_use_id="t1",
        name="bash",
        danger_level=DangerLevel.DESTRUCTIVE,
        rendered=doctored,
        reason="destructive",
    )
    shown = render_approval(request)
    assert not any(char in shown for char in (RLO, LRI, PDI))
    assert "<U+202E>" in shown


if __name__ == "__main__":  # pragma: no cover - convenience for a manual look
    asyncio.run(run_headless(hostile_stream(), output_format=OutputFormat.TEXT))  # type: ignore[arg-type]
