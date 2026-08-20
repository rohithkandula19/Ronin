"""Run `ronin.ui.demo`, the second of the three demos nothing executed.

`ronin.cli.demo` was the first (see `tests/cli/test_ronin_cli_demo.py`); `ronin.mcp.demo`
and `ronin.session_demo` are still not run by anything. This one is worth covering for a
reason the others are not: it is the only place the *rendering* claims are demonstrated
side by side, and rendering is where a regression is invisible to every other test.
`tests/ui` asserts that `render_diff` produces the right string; the demo asserts that a
person looking at nine sections can see the layer keeping its promises.

Nine sections, and the two that carry weight:

* **Section 6** renders `ApprovalRequest.rendered` *verbatim*, which is the demo's most
  load-bearing sentence and the one this audit changed. Verbatim is exactly right for the
  visible characters and exactly wrong for the invisible ones — see
  `test_ui_controls.py`. Both properties are asserted here, because "verbatim" and "cannot
  move the cursor" have to be simultaneously true or the approval block is either
  dishonest or unsafe.
* **Section 7** runs the same stream through the headless path and shows exit code 2 — an
  approval was needed and denied. A demo of a permission system that only ever shows
  approvals is demonstrating the half that cannot go wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ronin.ui import demo

#: Every section title, verbatim. A missing one is a subsystem that stopped being shown.
SECTIONS: tuple[str, ...] = (
    "1. the reducer never buffers, and StreamReset drops only its own span",
    "2. the transcript",
    "3. tool lines — one line each, including an unknown MCP tool",
    "4. a real unified diff, with colour markers, before approval",
    "5. todos and the status line",
    "6. the approval block renders ApprovalRequest.rendered, verbatim",
    "7. the same stream, headless: stream-json + exit code",
    "8. slash commands, including one from .ronin/commands",
    "9. answering an approval — which keys grant what, and which grant nothing",
    "10. the TUI is a thin skin over the same functions",
)


async def run(capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = await demo.main()
    return code, capsys.readouterr().out


# --------------------------------------------------------------------------- #
# it runs, and it shows everything it says it shows
# --------------------------------------------------------------------------- #


async def test_the_demo_runs_offline_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """No model, no network, no terminal: the event stream is a scripted list, which is
    the property `ui/__init__` names as the reason the layer is shaped this way."""
    code, out = await run(capsys)
    assert code == 0
    assert out.strip() != ""


async def test_every_section_is_reached(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = await run(capsys)
    for section in SECTIONS:
        assert section in out, f"section missing: {section}"


async def test_the_sections_appear_in_order(capsys: pytest.CaptureFixture[str]) -> None:
    """It reads as an argument, not a list of features — the reducer first because
    everything downstream is a function of its output."""
    _, out = await run(capsys)
    positions = [out.index(section) for section in SECTIONS]
    assert positions == sorted(positions), "the sections are out of order"


async def test_the_demo_writes_nothing_into_the_working_directory(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Section 8 loads a slash command from `.ronin/commands`, so the demo has a reason
    to touch a filesystem. It must be its own, not the caller's."""
    monkeypatch.chdir(tmp_path)
    await run(capsys)
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# section 1: the claim the whole layer rests on
# --------------------------------------------------------------------------- #


async def test_the_reducer_scene_shows_a_render_after_every_delta(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ "Never buffers" is what makes the TUI feel live rather than arriving in one
    block, and it is only visible as a sequence of partial states."""
    _, out = await run(capsys)
    assert "a render was available after every delta" in out


async def test_the_reset_scene_shows_the_dropped_span_is_gone(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The demo prints whether `DROPPED MID-SENTENCE` survived. It must say `False`:
    a mishandled `StreamReset` concatenates the pre- and post-retry text and the
    assistant appears to answer twice, which is the bug this scene exists to disprove."""
    _, out = await run(capsys)
    assert "'DROPPED MID-SENTENCE' still in the transcript: False" in out
    assert "stream resets seen: 1" in out


# --------------------------------------------------------------------------- #
# section 6: verbatim, and inert
# --------------------------------------------------------------------------- #


async def test_the_approval_block_shows_the_diff_it_is_asking_about(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ "Verbatim" is the promise: a user approving an edit is approving the diff in
    front of them, so the block cannot summarize or reflow it."""
    _, out = await run(capsys)
    body = out[out.index(SECTIONS[5]) :]
    assert "+def greet(name: str) -> None:" in body


async def test_nothing_the_demo_prints_can_move_a_terminal_cursor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The audit's own change, checked against the demo's real output.

    The demo is the one surface that renders with the `ANSI` styles, so it prints real
    escape sequences — which makes "no control characters" the wrong assertion and
    "no control characters we did not write" the right one. Every escape here must be
    an SGR colour code (`ESC [ … m`) or the reset, and nothing that clears, moves or
    talks to the window manager.
    """
    _, out = await run(capsys)

    import re

    for match in re.finditer("\x1b(.?)", out):
        assert match.group(1) == "[", f"non-CSI escape in demo output: {match.group(0)!r}"
    for sequence in re.findall(r"\x1b\[([0-9;]*)([A-Za-z])", out):
        assert sequence[1] == "m", f"non-colour CSI in demo output: ESC[{sequence[0]}{sequence[1]}"
    assert "\x1b]" not in out, "an OSC sequence reached the demo's output"
    assert "\r" not in out


# --------------------------------------------------------------------------- #
# section 7: the denial
# --------------------------------------------------------------------------- #


async def test_the_headless_scene_shows_a_denial_and_exit_code_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 2 is the contract a CI consumer checks, and the denial notice is what tells
    a human which call stopped. Both, or the scene proves half of it."""
    _, out = await run(capsys)
    body = out[out.index(SECTIONS[6]) :]
    assert "exit code: 2" in body
    assert "DENIED (non-interactive, no human attached)" in body


async def test_the_headless_scene_emits_parseable_json_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The scene's claim is `stream-json`, and the format is a contract a CI job parses.
    Printing something that merely looks like JSON would be the failure."""
    import json

    _, out = await run(capsys)
    body = out[out.index(SECTIONS[6]) : out.index(SECTIONS[7])]
    records = [
        json.loads(line)
        for line in body.splitlines()
        if line.startswith("{") and line.rstrip().endswith("}")
    ]
    assert records, "no JSON records in the headless scene"
    assert all("type" in record for record in records), "every record carries a type"
    assert {"turn_start", "text_delta"} <= {record["type"] for record in records}
