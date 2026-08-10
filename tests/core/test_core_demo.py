"""The demo command is a claim; this runs it so the claim stays true.

Every subsystem ships a demo by standing convention, and `docs/STACK.md` measured the
consequence: eleven `demo.py` modules at exactly 0% coverage, because no test runs one.
A demo nobody executes is documentation that compiles — it drifts the first time an
attribute is renamed, and the person who finds out is the one being shown the tool.

This one caught two real bugs on its first run: `StalledError.state` does not exist (the
attribute is `agent_state`), and a `KeyboardInterrupt` used as a stand-in BaseException
made pytest abort the session while reporting a pass.

So this asserts the demo's *observable claims*, not merely that it exits 0 — an exit code
alone would still pass if every scene printed nothing.
"""

from __future__ import annotations

import pytest

from ronin.core import demo
from ronin.core.protocols import ModelClient, Policy, ToolRegistry


async def test_the_demo_runs_end_to_end_offline(capsys: pytest.CaptureFixture[str]) -> None:
    assert await demo.main() == 0
    out = capsys.readouterr().out
    # All seven scenes reached.
    for heading in ("1.", "2.", "3.", "4.", "5.", "6.", "7."):
        assert heading in out
    assert "done" in out


async def test_the_demo_shows_the_stall_nudge_and_the_abort(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Scene 6 is the one the module docstring calls the point of the whole file."""
    await demo.scene_stall()
    out = capsys.readouterr().out
    assert "StalledError" in out
    assert "repeated the same action" in out  # the nudge text, naming the repetition
    assert "fingerprint" in out
    # The abort is still a checkpoint: the state carries answers for every call.
    assert "agent_state survives the abort" in out
    assert "answered call(s)" in out


async def test_the_demo_shows_parallel_then_serial(capsys: pytest.CaptureFixture[str]) -> None:
    """Read-only batches overlap; one mutating call forces order.

    `ScriptedTools.execute` awaits, so `max_in_flight` measures real overlap rather
    than the scheduler happening to interleave.
    """
    await demo.scene_parallel_then_serial()
    out = capsys.readouterr().out
    assert "max concurrent: 2" in out
    assert "max concurrent: 1" in out


async def test_the_demo_shows_every_outstanding_call_answered_on_interrupt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await demo.scene_interrupt()
    out = capsys.readouterr().out
    assert "interrupted" in out
    assert "tool results in the transcript: 2" in out


async def test_the_budget_scene_never_calls_the_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The budget check precedes the model call, so a refusal costs nothing."""
    await demo.scene_budget()
    assert "model calls made: 0" in capsys.readouterr().out


async def test_both_approval_answers_are_shown(capsys: pytest.CaptureFixture[str]) -> None:
    """Granted and refused. The refusal is the half a passing test suite hides."""
    await demo.scene_approval()
    out = capsys.readouterr().out
    assert "approval for rm" in out
    assert "approved, and the human was shown exactly" in out
    assert "DENIED" in out
    assert "the run did not end" in out


async def test_a_raising_tool_becomes_a_value(capsys: pytest.CaptureFixture[str]) -> None:
    await demo.scene_tool_raises()
    out = capsys.readouterr().out
    assert "FileNotFoundError" in out


def test_the_demos_doubles_satisfy_the_injected_protocols() -> None:
    """The point the demo is making, asserted rather than described.

    The doubles are not mocks of the real provider and tool layers — they are
    *instances of the protocols the loop accepts*. That is why a complete
    demonstration fits in one file with no configuration, and why `core` needs no
    import of `providers` or `tools` to have a runnable demo. (The dependency rule
    itself is enforced repo-wide by `tests/tools/test_boundaries.py`; asserting it
    again by grepping this file's text would be a brittle duplicate.)
    """
    assert isinstance(demo.ScriptedModel([]), ModelClient)
    assert isinstance(demo.ScriptedTools({}), ToolRegistry)
    assert isinstance(demo.ScriptedPolicy(), Policy)
