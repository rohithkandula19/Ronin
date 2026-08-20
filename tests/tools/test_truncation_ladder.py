"""The truncation ladder, end to end: what a real failing command tells the model.

A tool result passes through four independent caps on its way to the transcript —
``clamp_output`` in the shell (30k, head 40 / tail 60), ``Tool.execute``'s net (60k,
head-only), the gate's clamp (60k, head 60 / tail 40, successes only), and finally
``truncate_for_model`` in the loop (16k). The last one is smaller than all three
above it, so it is *always* the binding cut and its shape is the shape the model
actually sees.

Every layer had tests. The interaction had none, which is how two bugs lived here:

* a failing command's ``content`` was dropped in favour of its one-line ``error``,
  so ``pytest`` exiting 1 reached the model as ``command exited 1``;
* the final cut kept only the head, discarding the tail that the shell had split
  its budget specifically to preserve — and leaving the shell's "head and tail
  kept" marker asserting something that was no longer true.

These tests are the missing seam. Real bash, no network, and each one asserts on
the string that lands in the transcript rather than on any single layer's output.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ronin.core.loop import DEFAULT_MAX_TOOL_RESULT_CHARS, truncate_for_model
from ronin.core.types import ToolResult, ToolUse
from ronin.tools.base import MAX_RESULT_CHARS, ToolContext
from ronin.tools.registry import ToolRegistry
from ronin.tools.shell import (
    HEAD_SHARE,
    MAX_OUTPUT_CHARS,
    BashTool,
    PersistentShell,
    ShellSession,
    clamp_output,
)

# The traceback a Python failure actually ends with. The last line is the one that
# says what went wrong, and it is the last thing in the stream.
TRACEBACK_TAIL = "KeyError: 'user'"


def _transcript_text(result: ToolResult, *, limit: int = DEFAULT_MAX_TOOL_RESULT_CHARS) -> str:
    """The exact string the loop puts in the tool-role message, for one result."""
    return truncate_for_model(result.model_text(), limit)


async def _bash(command: str) -> ToolResult:
    """Run one command through the real tool, including ``Tool.execute``'s cap."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        shell = ShellSession(shell=PersistentShell(cwd=root, env={"PATH": "/usr/bin:/bin"}))
        registry = ToolRegistry((BashTool(shell),), ToolContext(root=root))
        try:
            return await registry.execute(
                ToolUse(id="t1", name="bash", arguments={"command": command})
            )
        finally:
            await shell.close()


# --------------------------------------------------------------------------- #
# a failing command explains itself
# --------------------------------------------------------------------------- #


async def test_a_failing_command_reaches_the_model_with_its_traceback() -> None:
    """The bug in one test: the model must be able to see why the command failed."""
    script = (
        'python3 -c "'
        "import sys; sys.stderr.write('Traceback (most recent call last):\\n');"
        f'sys.stderr.write(\\"{TRACEBACK_TAIL}\\n\\"); sys.exit(1)'
        '"'
    )
    result = await _bash(script)
    assert not result.ok, "the command must actually fail, or this test proves nothing"
    seen = _transcript_text(result)
    assert TRACEBACK_TAIL in seen
    assert result.error in seen, "the exit status is still reported alongside the output"


async def test_the_exit_status_leads_so_it_cannot_be_cut_away() -> None:
    result = await _bash("echo some output; (exit 3)")
    assert _transcript_text(result).startswith(result.error)


async def test_a_successful_command_carries_no_error_preamble() -> None:
    result = await _bash("echo hello")
    assert _transcript_text(result) == "hello"


# --------------------------------------------------------------------------- #
# the last cut does not undo the one below it
# --------------------------------------------------------------------------- #


async def test_a_huge_failure_keeps_the_end_where_the_reason_is() -> None:
    """40k lines of noise, then the reason. Head-only truncation loses the reason."""
    script = (
        'for i in $(seq 1 40000); do echo "noise line $i"; done; '
        f'echo "{TRACEBACK_TAIL}"; (exit 1)'
    )
    result = await _bash(script)
    assert not result.ok
    assert len(result.content) > DEFAULT_MAX_TOOL_RESULT_CHARS, "the cut must actually bind"
    seen = _transcript_text(result)
    assert len(seen) < len(result.content), "and it must actually have cut something"
    assert TRACEBACK_TAIL in seen
    assert "noise line 1\n" in seen, "the head survives too — both ends, not one"


def test_the_shells_head_and_tail_promise_survives_the_loops_cut() -> None:
    """``clamp_output`` claims "head and tail kept". After the loop, that must hold.

    This is the composition the ladder got wrong: two markers describing one string,
    where the outer cut silently falsified the inner one's claim.
    """
    body = "FIRST\n" + "x" * 400_000 + "\nLAST"
    clamped = clamp_output(body)
    assert "head and tail kept" in clamped, "precondition: the shell split its budget"
    seen = truncate_for_model(clamped)
    assert seen.startswith("FIRST")
    assert seen.rstrip().endswith("LAST")


def test_the_loop_cap_is_the_binding_one_so_its_shape_is_the_one_that_matters() -> None:
    # Written as an assertion rather than a comment: if a future change raises the
    # loop's cap above the layers below it, this stops being the deciding cut and
    # the reasoning in truncate_for_model's docstring no longer applies.
    assert DEFAULT_MAX_TOOL_RESULT_CHARS < MAX_OUTPUT_CHARS < MAX_RESULT_CHARS


def test_the_two_layers_bias_opposite_ways_on_purpose() -> None:
    # The shell is tail-heavy (a traceback is at the end of one command's output);
    # the model-facing cut is head-heavy (most results are file reads). Both keep
    # both ends, which is the only property the composition depends on.
    assert HEAD_SHARE < 0.5
    head, _, tail = truncate_for_model("A" * 5_000, 1_000).partition("\n…[")
    assert len(head) > len(tail.partition("]\n")[2]) > 0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
