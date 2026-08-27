"""Course-correction and failure, the last two frictions from the session audit.

Two independent things the audit found, both about a moment where the session went quiet
and left the user guessing:

* a correction typed mid-turn queued invisibly, and ``esc`` could not stop a command that
  was already running — the cooperative flag is only checked at loop boundaries, which a
  long build never reaches;
* an exhausted provider left the session as a raw Python stack trace, and a retry showed
  up as text vanishing mid-answer with no reason attached.

Offline: real bash for the cancel proof, scripted everything else. Nothing sleeps for the
duration it is testing.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import pytest

from ronin.cli.main import provider_failure_message
from ronin.core.loop import _execute_streaming
from ronin.core.types import StreamReset, ToolResult, ToolUse, TurnStart
from ronin.providers.types import ProviderError
from ronin.tools.base import ToolContext
from ronin.tools.registry import ToolRegistry
from ronin.tools.shell import BashTool, PersistentShell, ShellSession
from ronin.ui.reduce import RETRY_PREFIX, ViewState, reduce_all, reduce_event
from ronin.ui.render import PLAIN, render_panels, render_queued

# --------------------------------------------------------------------------- #
# a correction typed mid-turn is visible
# --------------------------------------------------------------------------- #


def test_a_queued_message_is_shown_with_when_it_will_run() -> None:
    state = ViewState().with_queued("actually use a dataclass")
    shown = render_queued(state, styles=PLAIN)
    assert "actually use a dataclass" in shown
    # The timing is the point: without it the user cannot decide whether to press esc.
    # It says "next step" and not "when this turn ends" because that is what now
    # happens — the message joins the running conversation at the loop's next
    # iteration rather than waiting for the whole turn.
    assert "esc" in shown and "next step" in shown


def test_several_queued_messages_are_counted_and_kept_in_order() -> None:
    state = ViewState().with_queued("first").with_queued("second")
    assert state.queued == ("first", "second")
    shown = render_queued(state, styles=PLAIN)
    assert "2 steering" in shown
    assert shown.index("first") < shown.index("second")


def test_blank_input_is_not_queued() -> None:
    assert ViewState().with_queued("   ").queued == ()


def test_the_queue_clears_when_the_next_turn_starts() -> None:
    state = ViewState().with_queued("do it differently")
    assert state.cleared_queued().queued == ()
    assert ViewState().cleared_queued().queued == ()  # already empty: a no-op


def test_nothing_queued_renders_nothing() -> None:
    assert render_queued(ViewState()) == ""
    assert render_panels(ViewState(), styles=PLAIN).queued == ""


def test_the_queue_has_its_own_region_and_is_not_transcript() -> None:
    panels = render_panels(
        ViewState(committed_text="the model's answer").with_queued("no, the other file"),
        styles=PLAIN,
    )
    assert "no, the other file" in panels.queued
    assert "no, the other file" not in panels.transcript


# --------------------------------------------------------------------------- #
# esc stops a tool that is already running
# --------------------------------------------------------------------------- #


def _bash_registry(root: Path) -> tuple[ToolRegistry, ShellSession]:
    shell = ShellSession(shell=PersistentShell(cwd=root, env={"PATH": "/usr/bin:/bin"}))
    return ToolRegistry((BashTool(shell),), ToolContext(root=root)), shell


async def test_interrupt_stops_a_command_that_is_already_running() -> None:
    """The whole point: a 30s command must not take 30s to abandon."""
    with tempfile.TemporaryDirectory() as td:
        registry, shell = _bash_registry(Path(td))
        cancelled = {"now": False}

        async def flip() -> None:
            await asyncio.sleep(0.3)
            cancelled["now"] = True

        asyncio.get_running_loop().create_task(flip())
        produced: list[ToolResult] = []
        started = time.monotonic()
        try:
            async for _event in _execute_streaming(
                registry,
                ToolUse(id="t1", name="bash", arguments={"command": "sleep 30; echo done"}),
                produced,
                cancelled=lambda: cancelled["now"],
            ):
                pass
        finally:
            await shell.close()
        elapsed = time.monotonic() - started

    # Generous: the command would take 30s uninterrupted, so anything under 10 proves
    # the interrupt landed, without asserting the runner's speed.
    assert elapsed < 10.0, f"esc did not stop the command (took {elapsed:.1f}s)"
    assert not produced[0].ok
    assert "interrupted" in produced[0].error  # a well-formed result, not an exception


async def test_the_killed_command_leaves_no_orphaned_children() -> None:
    """Killing only the shell would leave a child holding the pipe open."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        marker = root / "orphan-ran"
        registry, shell = _bash_registry(root)
        cancelled = {"now": False}

        async def flip() -> None:
            await asyncio.sleep(0.3)
            cancelled["now"] = True

        asyncio.get_running_loop().create_task(flip())
        produced: list[ToolResult] = []
        try:
            async for _event in _execute_streaming(
                registry,
                ToolUse(
                    id="t1",
                    name="bash",
                    # 4s, not 2: a loaded CI runner can delay the kill, and a margin
                    # that assumes a fast machine turns a real check into a flake.
                    arguments={"command": f"(sleep 4; touch {marker}) & wait"},
                ),
                produced,
                cancelled=lambda: cancelled["now"],
            ):
                pass
        finally:
            await shell.close()
        await asyncio.sleep(5.0)  # past when an orphan would have fired
        assert not marker.exists(), "the process group was orphaned instead of killed"


async def test_a_command_that_is_not_interrupted_still_completes_normally() -> None:
    with tempfile.TemporaryDirectory() as td:
        registry, shell = _bash_registry(Path(td))
        produced: list[ToolResult] = []
        try:
            async for _event in _execute_streaming(
                registry,
                ToolUse(id="t1", name="bash", arguments={"command": "echo hello"}),
                produced,
                cancelled=lambda: False,
            ):
                pass
        finally:
            await shell.close()
    assert produced[0].ok and "hello" in produced[0].content


# --------------------------------------------------------------------------- #
# an exhausted provider reads as a message, not a traceback
# --------------------------------------------------------------------------- #


def test_a_rate_limit_is_named_and_says_what_to_do() -> None:
    message = provider_failure_message(
        ProviderError(
            "https://api.example/v1 returned 429: slow down",
            provider="anthropic",
            retryable=True,
            retry_after="30",
        )
    )
    assert "rate limited" in message
    assert "anthropic" in message
    assert "wait 30" in message  # the server's own Retry-After, surfaced
    assert "/model" in message  # and the other way out
    assert "Traceback" not in message


def test_a_rate_limit_without_a_retry_after_still_reads_cleanly() -> None:
    message = provider_failure_message(ProviderError("429 too many requests"))
    assert "rate limited" in message
    assert "wait" in message


def test_an_unreachable_endpoint_points_at_configuration() -> None:
    message = provider_failure_message(
        ProviderError("connection refused", provider="ollama", retryable=True)
    )
    assert "could not be reached" in message
    assert "ollama" in message
    assert "persistent outage" in message  # retryable → not a bad request
    assert "doctor" in message


def test_a_refused_request_says_it_was_refused_outright() -> None:
    message = provider_failure_message(
        ProviderError("401 invalid api key", provider="openai", retryable=False)
    )
    assert "refused outright" in message
    assert "key" in message


def test_a_provider_error_with_no_detail_still_produces_a_sentence() -> None:
    message = provider_failure_message(ProviderError(""))
    assert "no detail" in message
    assert message.strip()


# --------------------------------------------------------------------------- #
# a retry explains itself instead of text vanishing
# --------------------------------------------------------------------------- #


def test_a_stream_reset_reports_why_the_text_disappeared() -> None:
    state = reduce_all((StreamReset(reason="retrying after: connection reset"),))
    assert state.resets == 1
    assert any(RETRY_PREFIX in notice for notice in state.notices)
    assert any("connection reset" in notice for notice in state.notices)


def test_a_reset_with_no_reason_adds_no_notice() -> None:
    state = reduce_all((StreamReset(),))
    assert state.resets == 1
    assert state.notices == ()


def test_a_reset_still_discards_only_its_own_span() -> None:
    # The original invariant must survive the new notice.
    state = ViewState(committed_text="settled prose ", span_text="dropped mid-sentence")
    after = reduce_event(state, StreamReset(reason="provider retry"))
    assert after.text == "settled prose "
    assert "dropped" not in after.text


def test_the_retry_notice_clears_when_the_next_turn_starts() -> None:
    state = reduce_all((StreamReset(reason="provider retry"),))
    assert state.notices
    assert state.cleared_notices().notices == ()
    # And the app clears on TurnStart, which the reducer leaves alone by design.
    assert reduce_event(state, TurnStart(turn_index=1)).notices == state.notices


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
