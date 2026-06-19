"""Background / async agent tasks — fire a long task and walk away.

``ronin ask`` / `ronin code` block the terminal until the agent finishes. For a
big task (a multi-step refactor plan, a deep investigation) you want to kick it
off and get on with your day, then be told when it lands. This module is that
"fire-and-forget" layer:

  * ``start_task`` launches the agent run in a DETACHED subprocess (its own
    session, stdio to /dev/null) and returns a short id immediately — the caller
    never blocks.
  * the spawned worker (``python -m ronin_cli.background --worker <id>``) re-reads
    the record, runs the task through ronin's own provider layer (``run_ask`` by
    default, ``run_code_agent`` for ``kind="code"``), stamps the result back to
    ``~/.ronin/bg/<id>.json``, and fires a "done" notification.
  * ``ronin bg --list`` / ``--result`` read those records back.

SAFETY: a background task is READ-ONLY by default (``run_ask``). A ``kind="code"``
run still goes through the normal approval/diff gate — the worker drives it with
no console and a denying gate, so it can PLAN and read but can never silently
auto-apply an edit while you're away. Nothing is auto-merged in the background.

The state machine + table rendering are pure and deterministic (times are passed
in), so they unit-test with no subprocess and no network. Everything that
touches disk is tolerant of a missing/empty ``bg`` dir.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# Status values for a background record's lifecycle. queued → running →
# done | failed (cancelled is a manual terminal state set by cancel_task).
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
_TERMINAL = {STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED}

# Allowed task kinds. "ask" → read-only Q&A agent (run_ask); "code" → the coding
# agent, but driven through the normal approval gate so it can never auto-apply.
_KINDS = ("ask", "code")


# --------------------------------------------------------------------------
# Paths (impure, but trivially so) — honor RONIN_HOME like bushido / gamify /
# the run store, defaulting to ~/.ronin. Tasks live one JSON file per id.
# --------------------------------------------------------------------------

def _home() -> Path:
    home = os.environ.get("RONIN_HOME")
    return Path(home) if home else Path.home() / ".ronin"


def bg_dir() -> Path:
    """Directory holding one ``<id>.json`` per background task."""
    return _home() / "bg"


def _record_path(task_id: str) -> Path:
    return bg_dir() / f"{task_id}.json"


def new_id() -> str:
    """A short, collision-resistant, shell-safe task id (e.g. 'bg-3f9a2c')."""
    return "bg-" + uuid.uuid4().hex[:6]


# --------------------------------------------------------------------------
# PURE state machine + rendering — no I/O, times injected, fully testable.
# --------------------------------------------------------------------------

def new_record(task_id: str, prompt: str, kind: str, *, now: float) -> dict:
    """Build the initial record for a queued task. Pure — ``now`` is the epoch
    seconds to stamp as creation time, so the result is deterministic.

    The record is the single source of truth the worker advances and the CLI
    reads back; it round-trips through JSON unchanged.
    """
    kind = kind if kind in _KINDS else "ask"
    return {
        "id": task_id,
        "prompt": prompt,
        "kind": kind,
        "status": STATUS_QUEUED,
        "created_at": float(now),
        "started_at": None,
        "ended_at": None,
        "elapsed": 0.0,
        "result": None,
        "error": None,
        "pid": None,
    }


def advance(
    record: dict,
    status: str,
    *,
    now: float,
    result: str | None = None,
    error: str | None = None,
) -> dict:
    """Return a NEW record advanced to ``status`` (pure — input untouched).

    Transitions stamp the right timestamps and keep ``elapsed`` honest:
      * → running  : stamps ``started_at`` (first time only).
      * → done/failed/cancelled : stamps ``ended_at`` and computes ``elapsed``
        from ``started_at`` (falling back to ``created_at`` if it never started),
        and records ``result`` / ``error``.
    ``now`` is the deciding clock so the same inputs always yield the same record.
    """
    rec = dict(record)
    rec["status"] = status

    # Pick the clock the run's duration is measured from. Use ``is None`` (not
    # truthiness) so a legitimate 0.0 epoch timestamp isn't treated as "unset".
    def _base() -> float:
        if rec.get("started_at") is not None:
            return float(rec["started_at"])
        if rec.get("created_at") is not None:
            return float(rec["created_at"])
        return float(now)

    if status == STATUS_RUNNING:
        # Stamp the start once; a re-entry (shouldn't happen) keeps the original.
        if rec.get("started_at") is None:
            rec["started_at"] = float(now)
        # An in-flight task's elapsed is "so far", so a --list mid-run is useful.
        rec["elapsed"] = max(0.0, float(now) - _base())
    elif status in _TERMINAL:
        rec["ended_at"] = float(now)
        rec["elapsed"] = max(0.0, float(now) - _base())
        if result is not None:
            rec["result"] = result
        if error is not None:
            rec["error"] = error
    return rec


def _fmt_elapsed(seconds: float) -> str:
    """Compact human elapsed: '4s', '2m13s', '1h05m'. Pure."""
    s = int(max(0.0, float(seconds)))
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m{sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def summarize_tasks(records: list[dict]) -> str:
    """A plain-text table (id / kind / status / elapsed / prompt) of ``records``.

    Pure: returns the rendered string (no console), so it's snapshot-testable and
    reusable from the CLI, a notification body, or a log. Newest first.
    """
    if not records:
        return "no background tasks yet — start one with `ronin bg \"<task>\"`."

    rows = sorted(records, key=lambda r: r.get("created_at", 0.0), reverse=True)
    headers = ("ID", "KIND", "STATUS", "ELAPSED", "PROMPT")
    table: list[tuple[str, str, str, str, str]] = []
    for r in rows:
        prompt = (r.get("prompt") or "").replace("\n", " ").strip()
        if len(prompt) > 50:
            prompt = prompt[:47] + "…"
        table.append((
            str(r.get("id", "?")),
            str(r.get("kind", "ask")),
            str(r.get("status", "?")),
            _fmt_elapsed(r.get("elapsed", 0.0)),
            prompt,
        ))

    widths = [len(h) for h in headers]
    for row in table:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def _line(cells: tuple[str, ...]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells)).rstrip()

    out = [_line(headers), _line(tuple("-" * w for w in widths))]
    out.extend(_line(row) for row in table)
    return "\n".join(out)


# --------------------------------------------------------------------------
# Persistence (impure) — one JSON file per task; tolerant of a missing dir or a
# corrupt/half-written file (a crashed worker must never break `--list`).
# --------------------------------------------------------------------------

def _write_record(record: dict) -> None:
    """Atomically persist a record to ``~/.ronin/bg/<id>.json``."""
    path = _record_path(str(record["id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic on POSIX → a reader never sees a half file
    except OSError:
        pass


def get_task(task_id: str) -> dict | None:
    """Load one record by id, or ``None`` if it's missing / unreadable."""
    path = _record_path(task_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_tasks() -> list[dict]:
    """Every readable background record (missing/empty dir → ``[]``). Newest
    first, so it pairs directly with ``summarize_tasks``."""
    d = bg_dir()
    if not d.is_dir():
        return []
    records: list[dict] = []
    for p in d.glob("bg-*.json"):
        try:
            records.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue  # skip a corrupt / half-written file rather than crash
    records.sort(key=lambda r: r.get("created_at", 0.0), reverse=True)
    return records


def cancel_task(task_id: str) -> bool:
    """Cancel a task: kill its worker process (if still running) and mark the
    record cancelled. Returns True if a task with that id existed.

    A task already in a terminal state is left as-is (returns True). Killing is
    best-effort — the record is the source of truth either way.
    """
    rec = get_task(task_id)
    if rec is None:
        return False
    if rec.get("status") in _TERMINAL:
        return True

    pid = rec.get("pid")
    if pid:
        import signal
        try:
            # Worker runs in its own session → killpg takes the whole tree.
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except (OSError, ProcessLookupError, PermissionError):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (OSError, ProcessLookupError, PermissionError):
                pass

    rec = advance(rec, STATUS_CANCELLED, now=time.time(),
                  error="cancelled by user")
    _write_record(rec)
    return True


# --------------------------------------------------------------------------
# Notification (impure) — reuse ronin's say (TTS) + the webhook notifier if
# present; always print a clear line so a "done" alert is never silent.
# --------------------------------------------------------------------------

def notify_done(record: dict, *, console: Any = None) -> None:
    """Announce that a background task finished.

    Reuses ronin's existing hooks when available — a one-line spoken alert via
    ``audio.speak`` (the same engine ``ronin say`` uses) and, if the user has set
    ``RONIN_NOTIFY_WEBHOOK``, a webhook ping via ``notify.post_webhook`` — and
    always prints a clear status line as the baseline. Never raises.
    """
    status = record.get("status", "?")
    tid = record.get("id", "?")
    ok = status == STATUS_DONE
    icon = "✅" if ok else "❌"
    line = (f"{icon} background task {tid} {status} "
            f"({_fmt_elapsed(record.get('elapsed', 0.0))}) — "
            f"see it with `ronin bg --result {tid}`")

    # 1) Always surface a visible line (rich console if handed one, else stdout).
    try:
        if console is not None:
            colour = "green" if ok else "#f7768e"
            console.print(f"[{colour}]{line}[/{colour}]")
        else:
            print(line)
    except Exception:  # noqa: BLE001 — a notice must never crash the worker
        pass

    # 2) Best-effort spoken alert, reusing the `ronin say` engine if one exists.
    try:
        from .audio import speak, tts_engine
        if tts_engine() is not None:
            verb = "finished" if ok else "failed"
            speak(f"ronin background task {verb}.")
    except Exception:  # noqa: BLE001
        pass

    # 3) Best-effort webhook ping (Slack/Discord/generic) if one is configured.
    try:
        url = os.environ.get("RONIN_NOTIFY_WEBHOOK", "")
        if url:
            from .notify import post_webhook
            post_webhook(url, line)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# Launching (impure) — detached subprocess; returns the id WITHOUT blocking.
# --------------------------------------------------------------------------

def start_task(prompt: str, *, root: str | Path = ".", kind: str = "ask") -> str:
    """Launch ``prompt`` as a DETACHED background agent run and return its id NOW.

    Writes a ``queued`` record, then spawns ``python -m ronin_cli.background
    --worker <id>`` in its own session with stdio redirected to a per-task log —
    so the worker outlives this call and this call never blocks. The worker takes
    it from there: running → done/failed, then a "done" notification.

    ``kind="ask"`` (default) runs the READ-ONLY agent. ``kind="code"`` runs the
    coding agent through its normal approval gate (no console → it can plan/read
    but the gate denies edits), so a background run never silently mutates files.
    """
    import subprocess

    kind = kind if kind in _KINDS else "ask"
    task_id = new_id()
    record = new_record(task_id, prompt, kind, now=time.time())
    # Stamp the working root so the worker (which starts fresh) runs in the right
    # repo. Kept in the record so it round-trips and `--result` can show it.
    record["root"] = str(Path(root).resolve())
    _write_record(record)

    log_path = bg_dir() / f"{task_id}.log"
    try:
        logf = open(log_path, "ab")
    except OSError:
        logf = subprocess.DEVNULL

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "ronin_cli.background", "--worker", task_id],
            cwd=record["root"],
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # detach: own process group, survives parent
            env=os.environ.copy(),
        )
    except Exception as e:  # noqa: BLE001 — surface a launch failure on the record
        rec = advance(record, STATUS_FAILED, now=time.time(),
                      error=f"failed to launch worker: {e.__class__.__name__}: {e}")
        _write_record(rec)
        if logf not in (subprocess.DEVNULL, None):
            try:
                logf.close()
            except OSError:
                pass
        return task_id

    # Record the pid so cancel_task can reach the worker's process group.
    record["pid"] = proc.pid
    _write_record(record)
    return task_id


# --------------------------------------------------------------------------
# Worker entrypoint — runs INSIDE the detached child; not called in-process by
# the CLI. Drives one task to completion through ronin's provider layer.
# --------------------------------------------------------------------------

def _run_worker(task_id: str) -> int:
    """Execute the queued task ``task_id`` to completion. Returns a process exit
    code (0 on a recorded outcome, even a failed task; 2 only if the record is
    missing). Always writes a terminal record and fires the notification."""
    from .config import load_config

    record = get_task(task_id)
    if record is None:
        return 2
    if record.get("status") in _TERMINAL:
        return 0  # already finished (e.g. cancelled before we started)

    record = advance(record, STATUS_RUNNING, now=time.time())
    record["pid"] = os.getpid()  # our own pid → cancel reaches THIS process group
    _write_record(record)

    config = load_config()
    root = record.get("root", ".")
    prompt = record.get("prompt", "")
    kind = record.get("kind", "ask")

    try:
        if kind == "code":
            # Coding agent, but headless: no console + a denying gate means it can
            # explore and PLAN, yet the approval gate blocks every edit — a
            # background run never auto-applies a diff. (read_only also forbids
            # mutating tools outright as a second belt.)
            from .code_mode import run_code_agent

            def _deny_gate(*_a: Any, **_k: Any) -> bool:
                return False  # auto-deny every approval prompt in the background

            res = run_code_agent(
                config, prompt, root=root, console=None,
                read_only=True, gate_cb=_deny_gate,
            )
        else:
            from .runner import run_ask
            res = run_ask(config, prompt, console=None)

        ok = bool(getattr(res, "success", True))
        output = str(getattr(res, "output", "") or "")
        err = getattr(res, "error", None)
        record = advance(
            record,
            STATUS_DONE if ok else STATUS_FAILED,
            now=time.time(),
            result=output,
            error=(None if ok else (err or "task reported failure")),
        )
    except Exception as e:  # noqa: BLE001 — any crash → a recorded failure
        record = advance(record, STATUS_FAILED, now=time.time(),
                         error=f"{e.__class__.__name__}: {e}")

    _write_record(record)
    notify_done(record)
    return 0


def main(argv: list[str] | None = None) -> int:
    """``python -m ronin_cli.background --worker <id>`` entrypoint (used by the
    detached child ``start_task`` spawns). Not part of the public API."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 2 and args[0] == "--worker":
        return _run_worker(args[1])
    sys.stderr.write("usage: python -m ronin_cli.background --worker <task_id>\n")
    return 2


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess
    raise SystemExit(main())
