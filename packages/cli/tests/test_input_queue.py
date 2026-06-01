"""Tests for the type-ahead InputQueue (send-while-working). The select+readline
cycle is injected so capture is tested deterministically, no real stdin/threads."""
from __future__ import annotations

from ro_claude_kit_cli.input_queue import InputQueue


def _iq(lines, *, ready=True, console=None):
    """An InputQueue whose poll yields ``lines`` one at a time."""
    box = list(lines)

    def select_fn():
        return ([object()] if (ready and box) else [], [], [])

    def readline_fn():
        return box.pop(0) if box else ""

    return InputQueue(console, select_fn=select_fn, readline_fn=readline_fn,
                      force_active=True)


def test_poll_captures_submitted_line() -> None:
    iq = _iq(["add dark mode\n"])
    iq._poll_once()
    assert iq.drain() == ["add dark mode"]


def test_drain_returns_in_order_and_clears() -> None:
    iq = _iq(["one\n", "two\n"])
    iq._poll_once()
    iq._poll_once()
    assert iq.drain() == ["one", "two"]
    assert iq.drain() == []  # drained once, now empty


def test_poll_ignores_when_not_ready() -> None:
    iq = _iq(["typed\n"], ready=False)
    iq._poll_once()
    assert iq.drain() == []


def test_poll_ignores_blank_line() -> None:
    iq = _iq(["   \n"])
    iq._poll_once()
    assert iq.drain() == []


def test_poll_ignores_eof() -> None:
    iq = _iq([])  # readline returns "" → EOF
    iq._poll_once()
    assert iq.drain() == []


def test_inactive_queue_is_noop() -> None:
    iq = InputQueue(force_active=False)
    iq.start()          # should not spawn a thread
    assert iq._thread is None
    iq.stop()
    assert iq.drain() == []


def test_context_manager_runs_and_stops() -> None:
    iq = _iq(["hello\n"])
    with iq:
        pass  # the background thread may or may not have polled; just must not error
    iq._poll_once()
    assert iq.drain() == ["hello"]


def test_echoes_queued_message_to_console() -> None:
    from rich.console import Console
    import io
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    iq = _iq(["queued idea\n"], console=console)
    iq._poll_once()
    assert "queued" in buf.getvalue().lower()
