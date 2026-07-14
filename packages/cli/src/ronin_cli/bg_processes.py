"""Background process management — long-running commands the agent can start,
watch, and stop without blocking the turn.

``run_command`` blocks until the command exits, which is wrong for a dev server,
a `--watch` build, or a test runner you want to keep alive. These tools let the
agent start such a process in the background, read its rolling logs, check
whether it's still up, and kill it — turning "fix-until-green" into
"watch-and-fix". Output (stdout+stderr merged) streams to a per-process log file
the agent tails.

Processes are started in their own session (process group) so killing one takes
down the whole tree (a dev server and its children), and an atexit hook stops
everything when ronin exits — no orphaned servers left holding ports.
"""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from ronin_agent_patterns import Tool


@dataclass
class _Proc:
    id: int
    command: str
    proc: subprocess.Popen
    log_path: Path


class BackgroundManager:
    """Tracks background processes for one ronin session."""

    def __init__(self) -> None:
        self._procs: dict[int, _Proc] = {}
        self._next = 1
        self._lock = threading.Lock()

    def start(self, command: str, cwd: str | Path = ".") -> int:
        with self._lock:
            pid = self._next
            self._next += 1
        fd, log = tempfile.mkstemp(prefix=f"ronin-bg-{pid}-", suffix=".log")
        os.close(fd)
        log_path = Path(log)
        logf = open(log_path, "wb")
        proc = subprocess.Popen(
            command, shell=True, cwd=str(cwd),
            stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group → killpg takes the whole tree
        )
        self._procs[pid] = _Proc(pid, command, proc, log_path)
        return pid

    def _state(self, p: _Proc) -> str:
        code = p.proc.poll()
        return "running" if code is None else f"exited({code})"

    def logs(self, pid: int, lines: int = 40) -> str:
        p = self._procs.get(pid)
        if p is None:
            return f"no background process #{pid} (use background_status to list them)"
        try:
            text = p.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        tail = "\n".join(text.splitlines()[-max(1, lines):])
        return (f"#{pid} [{self._state(p)}] {p.command}\n--- last {lines} lines ---\n"
                + (tail or "(no output yet)"))

    def status(self) -> str:
        if not self._procs:
            return "no background processes running"
        rows = [f"#{p.id} [{self._state(p)}] {p.command}" for p in self._procs.values()]
        return "\n".join(rows)

    def stop(self, pid: int) -> str:
        p = self._procs.get(pid)
        if p is None:
            return f"no background process #{pid}"
        if p.proc.poll() is None:
            try:
                os.killpg(os.getpgid(p.proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    p.proc.terminate()
                except OSError:
                    pass
            try:
                p.proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    os.killpg(os.getpgid(p.proc.pid), signal.SIGKILL)
                except OSError:
                    pass
        self._procs.pop(pid, None)
        return f"stopped background process #{pid} ({p.command})"

    def stop_all(self) -> None:
        for pid in list(self._procs):
            try:
                self.stop(pid)
            except Exception:  # noqa: BLE001 — cleanup must never raise
                pass


# One manager per ronin process (= per session). The atexit hook guarantees no
# background server outlives ronin.
_MANAGER: BackgroundManager | None = None


def get_manager() -> BackgroundManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = BackgroundManager()
        atexit.register(_MANAGER.stop_all)
    return _MANAGER


def build_background_tools(root: str | Path = ".") -> list[Tool]:
    """Four tools over the session's BackgroundManager. ``run_background`` is
    SENSITIVE (it runs a shell command) and gated like ``run_command``."""
    mgr = get_manager()

    def run_background(command: str) -> str:
        # Fail-closed: run_background starts the process on the HOST, so it cannot
        # honor a requested sandbox. If the user asked to contain (RONIN_BACKEND),
        # refuse rather than silently starting a long-running process outside the
        # sandbox — the same invariant run_command enforces.
        from .backends import requested_backend, sandbox_refusal
        _bspec = requested_backend()
        if _bspec:
            return sandbox_refusal(
                _bspec, "run_background starts the process on the host and cannot "
                "sandbox it; use run_command inside the backend, or unset RONIN_BACKEND")
        pid = mgr.start(command, cwd=root)
        return (f"started background process #{pid}: {command}\n"
                f"use background_logs(id={pid}) to read its output, "
                f"stop_background(id={pid}) to kill it.")

    def background_logs(id: int, lines: int = 40) -> str:
        return mgr.logs(int(id), int(lines))

    def background_status() -> str:
        return mgr.status()

    def stop_background(id: int) -> str:
        return mgr.stop(int(id))

    def _tool(name, desc, schema, handler) -> Tool:
        return Tool(name=name, description=desc, input_schema=schema, handler=handler)

    return [
        _tool("run_background",
              "Start a LONG-RUNNING command in the background (dev server, `--watch` build, "
              "test watcher) and return immediately with a process id — use this instead of "
              "run_command when the command doesn't exit on its own. SENSITIVE — gated by approval. "
              "Args: command.",
              {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
              run_background),
        _tool("background_logs",
              "Read the most recent output (stdout+stderr) of a background process. "
              "Args: id (process id), lines (default 40).",
              {"type": "object", "properties": {
                  "id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]},
              background_logs),
        _tool("background_status",
              "List the background processes and whether each is running or has exited.",
              {"type": "object", "properties": {}},
              background_status),
        _tool("stop_background",
              "Stop (kill) a background process and its child tree by id. Args: id.",
              {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
              stop_background),
    ]
