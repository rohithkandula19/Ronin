"""Type-ahead input queue — capture messages typed WHILE the agent is working.

The inline REPL is otherwise blocking: it reads input, runs the agent to
completion, then reads again — so anything typed during a turn is lost and you
"can't send while it's working". This runs a short-lived background reader (only
for the duration of one turn) that polls stdin via ``select`` so it's cleanly
stoppable, captures any full line you submit (Enter = send), and hands it back to
be processed as the next turn — the "send while it's working" behaviour of a
managed-input UI like Claude Code.

Only active on a real Unix TTY; a no-op otherwise (Windows / pipes / tests),
where the blocking REPL behaves exactly as before.
"""
from __future__ import annotations

import contextlib
import queue
import sys
import threading
import time
from typing import Callable

# The currently-running capture reader, if any. A blocking foreground prompt
# (the approval gate) pauses it via ``pause_capture()`` so the two don't race
# for the same stdin fd — otherwise the gate's input() and the reader can each
# steal the other's line (a 'y' echoed as a queued message, the prompt hangs).
_active_queue: "InputQueue | None" = None


@contextlib.contextmanager
def pause_capture():
    """Suspend the active input-capture reader for the duration of a blocking
    foreground prompt, then resume it. A no-op when no reader is active."""
    q = _active_queue
    if q is not None:
        q.pause()
    try:
        yield
    finally:
        if q is not None:
            q.resume()


def _default_select() -> tuple:
    import select
    return select.select([sys.stdin], [], [], 0.1)


class InputQueue:
    """Background, cancellable stdin reader. Use as a context manager around a
    turn; call :meth:`drain` afterwards to get whatever was typed during it."""

    def __init__(self, console=None, *, select_fn: Callable | None = None,
                 readline_fn: Callable | None = None, force_active: bool | None = None):
        self._q: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._console = console
        self._select_fn = select_fn or _default_select
        self._readline_fn = readline_fn or sys.stdin.readline
        self._active = force_active if force_active is not None else self._can_capture()

    @staticmethod
    def _can_capture() -> bool:
        """Only safe to grab stdin on an interactive Unix terminal."""
        try:
            import select  # noqa: F401 — probe availability (absent on Windows)
            return bool(sys.stdin.isatty() and sys.stdout.isatty())
        except Exception:
            return False

    def _poll_once(self) -> None:
        """One select+readline cycle. Factored out so it's unit-testable without
        threads. A submitted, non-empty line is queued (and echoed as queued)."""
        r, _, _ = self._select_fn()
        if not r:
            return
        line = self._readline_fn()
        if not line:
            return
        text = line.strip()
        if not text:
            return
        self._q.put(text)
        if self._console is not None:
            self._console.print(f"[dim]  ⏎ queued — will run after this turn:[/dim] {text}")

    def pause(self) -> None:
        """Stop reading stdin (a foreground prompt now owns it)."""
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.05)   # a foreground prompt owns stdin — don't touch it
                continue
            try:
                self._poll_once()
            except Exception:  # noqa: BLE001 — never let the reader crash a turn
                return

    def start(self) -> None:
        global _active_queue
        if not self._active:
            return
        self._stop.clear()
        self._paused.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        _active_queue = self

    def stop(self) -> None:
        global _active_queue
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None
        if _active_queue is self:
            _active_queue = None

    def drain(self) -> list[str]:
        """Return (and clear) every line captured during the turn, in order."""
        out: list[str] = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out

    def __enter__(self) -> "InputQueue":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
