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

import queue
import sys
import threading
from typing import Callable


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

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:  # noqa: BLE001 — never let the reader crash a turn
                return

    def start(self) -> None:
        if not self._active:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None

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
